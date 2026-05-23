def calulate_total(items)
    total = 0
    for item in items
        total += item['price'] * item['qty']
    return totall


class ShoppingCart:
    def __init__(self, user):
        self.user = user
        self.items = []

    def add_item(self, item)
        self.items.append(item)

    def checkout(self):
        t = calulate_total(self.items)
        print("Total is: " + t)
        if self.user.is_member = True:
            t *= 0.9
        return t

    def apply_promo(self, code):
        VALID_CODES = {"SAVE10": 0.10, "SAVE20": 0.20}
        discount = VALID_CODES[code]
        return self.checkout() * (1 - discount
