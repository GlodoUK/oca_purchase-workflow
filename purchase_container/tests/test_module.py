from datetime import timedelta

from odoo import Command, fields

from odoo.addons.base.tests.common import BaseCommon


class Test(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.product"].create({"name": "Test Product"})
        cls.cont_a = cls.env["purchase.container"].create({"code": "AA"})
        cls.cont_b = cls.env["purchase.container"].create({"code": "BB"})
        cls.cont_c = cls.env["purchase.container"].create({"code": "CC"})
        cls.incoterm_id = cls.env.ref("account.incoterm_FCA")

    def test_container_by_purchase(self):
        # first PO
        po = self.get_po()
        po.button_confirm()
        pick01 = po.picking_ids[0]
        pick01.container_id = self.cont_a.id
        self.assertIn(self.cont_a, po.container_ids)
        self.assertEqual(self.cont_a.purchase_order_count, 1)
        pick01.button_validate()
        # this update triggers a new picking
        po.order_line[0].product_qty = 7
        pick02 = po.picking_ids.filtered(lambda x: x.state != "done")
        pick02.container_id = self.cont_b.id
        pick02.button_validate()
        self.assertEqual(pick02.state, "done")
        self.assertIn(self.cont_b, po.container_ids)
        self.assertEqual(self.cont_b.purchase_order_count, 1)
        # second PO
        po2 = po.copy()
        po2.button_confirm()
        pick11 = po2.picking_ids
        pick11.container_id = self.cont_b.id
        pick11.button_validate()
        self.assertIn(self.cont_b, po2.container_ids)
        self.assertEqual(pick11.state, "done")
        self.assertEqual(len(self.cont_b.purchase_order_ids), 2)
        self.env.flush_all()
        self.assertEqual(self.cont_b.purchase_order_count, 2)
        self.assertEqual(self.cont_b.picking_count, 2)
        self.assertEqual(self.cont_b.displayed_incoterm_id, self.incoterm_id)

    def test_incoterm(self):
        po = self.get_po()
        self.cont_a.purchase_order_ids = [Command.link(po.id)]
        self.assertEqual(self.cont_a.incoterm_id, self.incoterm_id)
        self.assertEqual(self.cont_a.displayed_incoterm_id, self.incoterm_id)
        exw = self.env.ref("account.incoterm_EXW")
        po.incoterm_id = exw
        self.assertEqual(self.cont_a.incoterm_id, exw)
        self.assertEqual(self.cont_a.displayed_incoterm_id, exw)
        # A manual incoterm wins over the purchase order incoterm.
        cip = self.env.ref("account.incoterm_CIP")
        self.cont_a.displayed_incoterm_id = cip
        self.assertEqual(self.cont_a.manual_incoterm_id, cip)
        po.incoterm_id = self.incoterm_id
        self.assertEqual(self.cont_a.displayed_incoterm_id, cip)

    def test_date_ett(self):
        self.assertFalse(self.cont_a.date_ett)
        self.cont_a.write(
            {
                "date_etd": fields.Date.to_date("2026-01-01"),
                "date_eta": fields.Date.to_date("2026-01-10"),
            }
        )
        self.assertEqual(self.cont_a.date_ett, "9 days, 0:00:00")

    def test_purchase_order_counts_multi(self):
        """Count RFQs and orders per container on a multi-record set."""
        rfq = self.get_po()
        order_1 = self.get_po()
        order_1.button_confirm()
        order_2 = self.get_po()
        order_2.button_confirm()
        cancelled = self.get_po()
        cancelled.button_cancel()
        self.cont_a.purchase_order_ids = [Command.set((rfq | order_1).ids)]
        self.cont_b.purchase_order_ids = [Command.set((rfq | order_1 | order_2).ids)]
        self.cont_c.purchase_order_ids = [Command.set(cancelled.ids)]

        containers = self.cont_a | self.cont_b | self.cont_c
        self.assertEqual(containers.mapped("purchase_order_rfq_count"), [1, 1, 0])
        self.assertEqual(containers.mapped("purchase_order_count"), [1, 2, 0])

        # Draft records use their origin, new records count nothing.
        draft = self.env["purchase.container"].new(origin=self.cont_b)
        self.assertEqual(draft.purchase_order_count, 2)
        self.assertEqual(draft.purchase_order_rfq_count, 1)
        new = self.env["purchase.container"].new({"code": "NEW"})
        self.assertEqual(new.purchase_order_count, 0)
        self.assertEqual(new.purchase_order_rfq_count, 0)

        # Counts follow state changes without a manual recompute.
        rfq.button_cancel()
        self.assertEqual(self.cont_a.purchase_order_rfq_count, 0)
        self.assertEqual(self.cont_a.purchase_order_count, 1)
        self.assertEqual(self.cont_b.purchase_order_rfq_count, 0)
        self.assertEqual(self.cont_b.purchase_order_count, 2)

    def test_action_views(self):
        rfq = self.get_po()
        po = self.get_po()
        po.button_confirm()
        pick01 = po.picking_ids[0]
        pick01.container_id = self.cont_a.id
        self.env.flush_all()
        self.cont_a.purchase_order_ids = [Command.link(rfq.id)]
        action = self.cont_a.action_view_rfq()
        self.assertEqual(action["domain"], [("id", "in", rfq.ids)])
        action = self.cont_a.action_view_order()
        self.assertEqual(action["domain"], [("id", "in", po.ids)])
        action = self.cont_a.action_view_picking()
        self.assertEqual(action["domain"], [("id", "in", pick01.ids)])

    def test_state(self):
        today = fields.Date.today()
        cont = self.cont_a
        self.assertEqual(cont.state, "waiting")
        cont.date_etd = today + timedelta(days=5)
        self.assertEqual(cont.state, "waiting")
        # The actual departure date wins over the estimated one.
        cont.date_atd = today - timedelta(days=1)
        self.assertEqual(cont.state, "transit")
        cont.button_lock()
        self.assertEqual(cont.state, "locked")
        cont.button_unlock()
        self.assertEqual(cont.state, "transit")
        po = self.get_po()
        po.button_confirm()
        po.picking_ids.container_id = cont
        self.assertEqual(cont.state, "transit")
        po.picking_ids.button_validate()
        self.assertEqual(cont.state, "arrived")

    def test_code_and_name(self):
        cont = self.env["purchase.container"].create({"name": "msku 1234567"})
        self.assertEqual(cont.code, "MSKU")
        cont.code = "abc"
        self.assertEqual(cont.code, "ABC")
        self.assertEqual(cont.name, "ABC")
        po = self.get_po()
        cont.purchase_order_ids = [Command.link(po.id)]
        self.assertEqual(cont.name, f"ABC ({po.name})")

    def get_po(self):
        return self.env["purchase.order"].create(
            {
                "partner_id": self.partner.id,
                "date_planned": fields.Datetime.now(),
                "incoterm_id": self.incoterm_id.id,
                "order_line": [
                    Command.create(
                        {
                            "name": "Test Line",
                            "product_id": self.product.id,
                            "product_qty": 4.0,
                            "product_uom_id": self.product.uom_id.id,
                            "price_unit": 1,
                            "date_planned": fields.Datetime.now(),
                        },
                    )
                ],
            }
        )
