# Grade: F — multiple syntax errors prevent this module from loading.
# Tests the tool's behavior when ast.parse raises SyntaxError.
#
# Run: code-explain --analyze e2e_tests/01_python_syntax_errors.py

def calulate_total(items)       # SyntaxError: missing colon
    total = 0
    for item in items           # SyntaxError: missing colon
        total += item['price'] * item['qty']
    return totall               # NameError at runtime: misspelled name


class ShoppingCart:
    def __init__(self, user):
        self.user = user
        self.items = []

    def add_item(self, item)    # SyntaxError: missing colon
        self.items.append(item)

    def checkout(self):
        t = calulate_total(self.items)
        print("Total is: " + t)     # TypeError: str + int (caught at runtime, not parse time)
        if self.user.is_member = True:   # SyntaxError: assignment in condition
            t *= 0.9
        return t

    def apply_promo(self, code):
        VALID_CODES = {"SAVE10": 0.10, "SAVE20": 0.20}
        discount = VALID_CODES[code]    # KeyError if code not found — no .get()
        return self.checkout() * (1 - discount
                                  # SyntaxError: unclosed parenthesis
