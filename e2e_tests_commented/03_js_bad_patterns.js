// Grade: D — valid JS (parses fine) but packed with antipatterns, bugs, and security smells.
//
// Run: code-explain --analyze e2e_tests/03_js_bad_patterns.js

var API_KEY = "sk-prod-abc123xyz789";   // hardcoded secret committed to source

// Synchronous XHR blocks the browser's main thread; eval() executes server response
function fetchUserData(userId) {
    var url = "https://api.example.com/users/" + userId;
    var xhr = new XMLHttpRequest();
    xhr.open("GET", url, false);    // false = synchronous — freezes the page
    xhr.send();

    if (xhr.status == 200) {        // loose equality (should be ===)
        var data = eval(xhr.responseText);  // eval of untrusted server data
        return data
    }
    return null                     // silently swallows non-200 status
}

// Bitwise & instead of logical &&; encoding password ≠ encrypting it
function processUsers(users) {
    var result = [];
    for (var i = 0; i < users.length; i++) {
        var user = users[i];
        if (user.role == "admin" & user.active == true) {   // & is bitwise AND
            result.push({
                id: user.id,
                name: user.name,
                token: btoa(user.password),   // base64 is reversible — not secure
            });
        }
    }
    return result
}

// Classic callback hell — errors silently swallowed at each level
function loadDashboard(userId, callback) {
    fetchUser(userId, function(err, user) {
        // err never checked — crash if fetchUser fails
        fetchOrders(user.id, function(err, orders) {
            fetchProducts(orders[0].id, function(err, products) {  // IndexError if orders is empty
                fetchInventory(products[0].sku, function(err, inv) {
                    callback(null, { user, orders, products, inv });
                });
            });
        });
    });
}

// innerHTML with unsanitised input — DOM-based XSS
function renderComment(comment) {
    var el = document.getElementById("comments");
    el.innerHTML += "<p>" + comment.text + "</p>";   // XSS: comment.text can contain <script>
}

// Prototype pollution via Object.assign with user-supplied JSON
function mergeConfig(defaults, userConfig) {
    return Object.assign(defaults, JSON.parse(userConfig));  // overwrites __proto__ if userConfig contains it
}

// Global variable leak; no var/let/const
function calculateTotal(items) {
    total = 0;                  // implicit global
    for (var i = 0; i < items.length; i++) {
        total += items[i].price * items[i].qty;
    }
    return total;
}
