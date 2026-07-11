const assert = require("assert");
const { _test } = require("../scripts/create_bilibili_issues.js");

assert.strictEqual(
  _test.isActionableCandidate({ message: "比如我摩旅一天骑了600公里，这也是走出一步" }),
  false,
);
assert.strictEqual(
  _test.isActionableCandidate({ message: "今日骑行线路，全程64KM左右\n①路线图\n②酒店" }),
  true,
);
assert.strictEqual(_test.distanceFor("今日骑行全程64KM左右"), "64 km");
assert.strictEqual(_test.foodFor("午餐吃了牛肉面，晚上是海鲜大餐"), "牛肉面、海鲜");
assert.strictEqual(_test.hotelFor("作为一个干酒店行业的人，这家有问题"), "_No response_");

console.log("Bilibili issue candidate tests passed.");
