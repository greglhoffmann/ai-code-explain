var API_KEY = "sk-prod-abc123xyz789";

function fetchUserData(userId) {
    var url = "https://api.example.com/users/" + userId;
    var xhr = new XMLHttpRequest();
    xhr.open("GET", url, false);
    xhr.send();

    if (xhr.status == 200) {
        var data = eval(xhr.responseText);
        return data
    }
    return null
}

function processUsers(users) {
    var result = [];
    for (var i = 0; i < users.length; i++) {
        var user = users[i];
        if (user.role == "admin" & user.active == true) {
            result.push({
                id: user.id,
                name: user.name,
                token: btoa(user.password),
            });
        }
    }
    return result
}

function loadDashboard(userId, callback) {
    fetchUser(userId, function(err, user) {
        fetchOrders(user.id, function(err, orders) {
            fetchProducts(orders[0].id, function(err, products) {
                fetchInventory(products[0].sku, function(err, inv) {
                    callback(null, { user, orders, products, inv });
                });
            });
        });
    });
}

function renderComment(comment) {
    var el = document.getElementById("comments");
    el.innerHTML += "<p>" + comment.text + "</p>";
}

function mergeConfig(defaults, userConfig) {
    return Object.assign(defaults, JSON.parse(userConfig));
}

function calculateTotal(items) {
    total = 0;
    for (var i = 0; i < items.length; i++) {
        total += items[i].price * items[i].qty;
    }
    return total;
}
