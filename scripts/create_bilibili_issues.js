const fs = require("fs");

const ROUTE_LABEL = "route-data";
const REVIEW_LABEL = "待核验";
const AUTO_LABEL = "auto-bilibili";

const LABELS = [
  {
    name: ROUTE_LABEL,
    color: "16806c",
    description: "路线、里程、饮食、住宿等数据建议",
  },
  {
    name: REVIEW_LABEL,
    color: "d4a72c",
    description: "尚未由维护者核对视频内容",
  },
  {
    name: AUTO_LABEL,
    color: "6f42c1",
    description: "由 B 站公开视频评论自动生成",
  },
];

const KEYWORDS =
  /路线图|路线|路书|骑行|地址|位置|友谊关|友誼關|凭祥|口岸|海关|起点|终点|出发|到达|公里|里程|\bKM\b|酒店|住宿|入住|民宿|旅馆|宾馆|Hotel|早餐|午餐|晚餐|餐厅|餐馆|小吃|猪杂粉|海鲜|烤肉|烧烤|咖啡店|茶馆|\d+(?:\.\d+)?\s*(?:元|人民币|CNY|万?越南盾|VND)/i;

function readCandidates(path = "bilibili-candidates.json") {
  if (!fs.existsSync(path)) return [];
  const raw = fs.readFileSync(path, "utf8").replace(/^\uFEFF/, "");
  return JSON.parse(raw).items || [];
}

function short(text, max = 46) {
  const value = String(text || "").trim();
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function categoryFor(message) {
  const text = String(message || "");
  if (/酒店|住宿|入住|民宿|旅馆|宾馆|Hotel/i.test(text)) return "住宿";
  if (/公里|里程|\bKM\b/i.test(text)) return "当天里程";
  if (/吃|早餐|午餐|晚餐|粉|饭|餐厅|餐馆|小吃|水果|茶|咖啡|烧烤|烤肉|海鲜/i.test(text)) return "美食";
  if (/起点|终点|出发|到达|口岸|海关|路线|路书|位置|地点|城市|村|县|省/i.test(text)) return "地点 / 路线";
  return "关键内容 / 事件";
}

function distanceFor(message) {
  const match = String(message || "").match(/(\d+(?:\.\d+)?)\s*(?:KM|公里)/i);
  return match ? `${match[1]} km` : "_No response_";
}

function hotelFor(message) {
  const text = String(message || "");
  const match =
    text.match(/(?:酒店(?:为|叫)|入住(?:的)?|住的酒店(?:是|叫)?|名称[:：]?)\s*([A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff\s·.'-]{1,32}(?:Hotel|酒店|宾馆|旅馆|民宿))/i) ||
    text.match(/\b([A-Za-z][A-Za-z\s·.'-]{1,30}Hotel)\b/i);
  return match ? match[1].trim() : "_No response_";
}

function foodFor(message) {
  const matches = String(message || "").match(
    /猪杂粉|牛肉粉|牛肉面|河粉|米粉|烤肉|烧烤|龙虾|肉蟹|海鲜|法棍|春卷|鸡饭|咖啡|Jollibee|蜜雪冰城/gi,
  );
  return matches ? [...new Set(matches)].join("、") : "_No response_";
}

function isActionableCandidate(candidate) {
  const text = String(candidate.message || "").trim();
  if (!text) return false;
  const firstPersonDistance = /(?:比如|例如|我摩旅|我曾经|我之前|我花了|我一天|本人).{0,30}\d+(?:\.\d+)?\s*(?:KM|公里)/i;
  const episodeDistance = /(?:今日|今天|当天|本期|第\S+天).{0,30}(?:骑行|全程).{0,20}\d+(?:\.\d+)?\s*(?:KM|公里)|(?:骑行全程|全程骑行)\s*\d+(?:\.\d+)?\s*(?:KM|公里)/i;
  if (firstPersonDistance.test(text) && !episodeDistance.test(text)) return false;
  const structuredSummary = /(?:^|\n)\s*(?:①|1[.、]|路线图|今日骑行|今天骑行|当天骑行|本期骑行|第\S+天)|\d{1,2}:\d{2}|省流|时间线/i;
  const explicitLodging = /酒店(?:为|叫)|入住(?:的)?酒店|住的酒店|民宿(?:为|叫)|\b[A-Za-z][A-Za-z\s·.'-]{1,30}Hotel\b/i;
  const explicitRoute = /(?:今日|今天|当天).{0,20}(?:到达|来到|路线)|从.{1,24}(?:到|→|—).{1,24}|骑行全程|全程骑行/i;
  const explicitFood = /吃了|早餐店|午餐店|晚餐店|推荐菜|店名|猪杂粉|牛肉粉|牛肉面|河粉|龙虾|肉蟹|海鲜大餐/i;
  return structuredSummary.test(text) || explicitLodging.test(text) || explicitRoute.test(text) || explicitFood.test(text);
}

function hotelPriceFor(message) {
  const match = String(message || "").match(/(\d+(?:\.\d+)?)\s*(CNY|人民币|元|万?越南盾|VND)/i);
  return match ? `${match[1]} ${match[2]}` : "_No response_";
}

function buildIssueBody(candidate, category, rpid) {
  return [
    "> 此建议由系统从公开视频下方的高赞评论自动整理。请维护者核对视频后再添加“已采纳”；需要时可先编辑 Issue，补全结构化字段。",
    "",
    "### B站视频链接或 BV 号",
    "",
    `https://www.bilibili.com/video/${candidate.bvid}/`,
    "",
    "### 内容类型",
    "",
    category,
    "",
    "### 视频日期（可选）",
    "",
    candidate.date || "_No response_",
    "",
    "### 你发现了什么？",
    "",
    candidate.message || "_No response_",
    "",
    "### 视频时间点（可选）",
    "",
    "_No response_",
    "",
    "### 地点或路段（可选）",
    "",
    "_No response_",
    "",
    "### 当天里程（如有）",
    "",
    distanceFor(candidate.message),
    "",
    "### 经纬度（如有）",
    "",
    "_No response_",
    "",
    "### 视频里吃了什么（如有）",
    "",
    category === "美食" ? foodFor(candidate.message) : "_No response_",
    "",
    "### 酒店 / 民宿名称（如有）",
    "",
    category === "住宿" ? hotelFor(candidate.message) : "_No response_",
    "",
    "### 住宿所在区域（如有）",
    "",
    category === "住宿" ? candidate.place || "_No response_" : "_No response_",
    "",
    "### 住宿价格与币种（如有）",
    "",
    category === "住宿" ? hotelPriceFor(candidate.message) : "_No response_",
    "",
    "### 更多依据（可选）",
    "",
    `B站评论作者：${candidate.user || "未知"}\n\nB站点赞：${candidate.likes || 0}\n\n原评论：${candidate.url}`,
    "",
    `<!-- bili-rpid:${rpid} -->`,
  ].join("\n");
}

async function ensureLabels({ github, owner, repo }) {
  for (const label of LABELS) {
    try {
      await github.rest.issues.getLabel({ owner, repo, name: label.name });
    } catch {
      await github.rest.issues.createLabel({ owner, repo, ...label });
    }
  }
}

async function knownCommentIds({ github, owner, repo }) {
  const issues = await github.paginate(github.rest.issues.listForRepo, {
    owner,
    repo,
    state: "all",
    labels: AUTO_LABEL,
    per_page: 100,
  });
  const known = new Set();
  for (const issue of issues) {
    const match = String(issue.body || "").match(/<!--\s*bili-rpid:(\d+)\s*-->/);
    if (match) known.add(match[1]);
  }
  return known;
}

async function run({ github, context, core }) {
  const owner = context.repo.owner;
  const repo = context.repo.repo;
  const rawCandidates = readCandidates();
  const candidates = rawCandidates
    .filter((candidate) => Number(candidate.likes || 0) >= 10)
    .filter((candidate) => KEYWORDS.test(candidate.message || ""))
    .filter(isActionableCandidate);

  await ensureLabels({ github, owner, repo });
  const knownRpids = await knownCommentIds({ github, owner, repo });

  let created = 0;
  const maxNewPerRun = 10;
  for (const candidate of candidates) {
    const rpid = String(candidate.rpid || "");
    if (!rpid || knownRpids.has(rpid) || created >= maxNewPerRun) continue;

    const category = categoryFor(candidate.message);
    await github.rest.issues.create({
      owner,
      repo,
      title: `[B站评论 👍${candidate.likes || 0}] ${candidate.date || ""} · ${short(candidate.message || candidate.bvid)}`,
      body: buildIssueBody(candidate, category, rpid),
      labels: [ROUTE_LABEL, REVIEW_LABEL, AUTO_LABEL],
    });
    knownRpids.add(rpid);
    created++;
  }

  core.info(`Created ${created} new Issue(s) from ${candidates.length} candidate(s).`);
}

module.exports = {
  run,
  _test: { distanceFor, foodFor, hotelFor, isActionableCandidate },
};
