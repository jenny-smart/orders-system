import unittest

from memo_system import change_order as c


class DoneChargePaywayTest(unittest.TestCase):
    def _item(self, payway):
        row = [""] * 31
        row[1], row[6], row[12], row[13], row[17] = "已收款", "LC002176601", "2026/10/23", "600", payway
        return {"status": "已收款", "raw": row}

    def test_blank_r_is_rejected_before_touching_form(self):
        form = {"chargePayment": "原本"}
        with self.assertRaisesRegex(ValueError, "R 欄付款方式空白"):
            c.apply_sheet_row_to_form(form, {}, self._item(""))
        self.assertEqual(form, {"chargePayment": "原本"})

    def test_r_fills_charge_payment(self):
        form = {"chargePayment": ""}
        controls = {"chargePayment": [{"type": "text", "context": "加收金流"}]}
        c.apply_sheet_row_to_form(form, controls, self._item("ATM"))
        self.assertEqual(form["chargePayment"], "ATM")


if __name__ == "__main__":
    unittest.main()
