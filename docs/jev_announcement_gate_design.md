# Jev 公告正文增强 + 门控 施工规格

> 版本：v1.1（评审后修订）
> 日期：2026-09-22（初稿）/ 2026-09-23（评审修订）
> 状态：**评审通过，待实施**
> 上游：`docs/jev_news_gate_design.md`（新闻门控已落地）、`docs/jev_adaptive_gate_design.md`（**已过时**，仅作历史记录）

---

## 0. 评审记录（2026-09-23）

v1.0-draft 经代码级评审（逐条对照已落地的 `news_gate.py` / `typesafe_client.py` /
`akshare_vendor.py` 的真实签名与读取的配置键）识别出 3 个 BLOCKER、3 个 MAJOR、
4 个 MINOR，v1.1 全部修正：

| # | 严重度 | v1.0 问题 | v1.1 修正 |
|---|--------|----------|----------|
| 1 | **BLOCKER** | 声称"两者独立开关"，但被复用的 `apply_news_gate` / `TypeSafeNewsGate` 内部**只读 `jev_news_gate_*`**（`news_gate.py:29,32,35,46`、`typesafe_client.py:40-41`）—— 只开 `jev_ann_gate_enabled` 时门控根本不执行；同时开两个开关则公告静默继承新闻的 threshold / timeout / keep_floor | 两者增加 keyword-only `config_prefix` 参数（默认 `"jev_news_gate"` → 新闻路径零变化），公告侧传 `"jev_ann_gate"`；`jev_api_key_env` / `jev_model` 保持共享不加前缀 |
| 2 | **BLOCKER** | §5.2 规则 1 要"**正文哈希**去重"，但 §5.4 代码把 `dedup_announcements` 放在 `fetch_bodies` **之前**，且 §5.2 的函数签名根本没有 bodies 参数 —— 规则不可实现 | 身份键改为抓取**之前**就可得的公告标识：优先 `_art_code`，退回 `网址`，再退回 `公告标题+公告日期`。效果等价（东财同一公告 art_code 唯一），且省一次抓取 |
| 3 | **BLOCKER** | §5.4 的 `dedup_announcements` **无条件执行**，只有抓正文被开关挡住 —— 关闭开关仍会吞掉重复公告，直接违反 §6 第 1 行"完全现状"与 §5.6 测试 6"逐字一致" | dedup 只在 `enabled AND NOT shadow` 时参与渲染过滤；关闭与 shadow 下渲染行集恒为全量 `rows` |
| 4 | MAJOR | §4.1 只提参数化 `question` / `criteria`，漏了三处硬编码：`_BODY_TRUNC = 500`（公告要 800）、`instructions["article"]` 键名、`TypeSafeNewsGate` 读的 `timeout` / `max_articles` | §4.1 列全 4 类参数化点；§4.3 请求体键名统一为 `article`（与已落地代码一致，**不再自造 `announcement` 键**） |
| 5 | MAJOR | §6 第 10 行引用 `max_articles`，但 §5.5 配置清单里**没有** `jev_ann_gate_max_articles`（会静默继承新闻的 30） | 新增该键；同时把 §5.5 原 `timeout` 澄清为 **Jev 评分超时**（与新闻门控同名同义），抓取超时另立 `jev_ann_gate_fetch_timeout` —— 否则 `config_prefix` 机制下两个语义会撞在同一个键上 |
| 6 | MAJOR | §6 第 2 行"无 API key → 不抓正文"把**信号恢复**绑死在 Jev key 上，与 §1"信号被截断才是核心问题"冲突；`fetch_bodies` 在无 key 时也因此永远无效 | 抓取只由 `enabled AND fetch_bodies` 控制；**无 key 时仍抓取并渲染正文，仅跳过评分与降级**（+ warning 日志）。与已落地新闻门控语义一致：key 缺失只影响"是否降级"，不影响"内容是否交付" |
| 7 | MINOR | `extract_art_code` 每行调两次，且 `or ""` 会产生 `bodies.get("")` 空查 | 抓取前一次性把 art_code 写进 row（`_art_code`），后续只读 |
| 8 | MINOR | 未说明 `notice_content` 为空或含 HTML 时的行为 | `fetch_bodies` 缓存**已剥离标签的纯文本**；strip 后为空视为抓取缺失（不进返回 dict）；渲染时该条退回标题-only。测试矩阵补 1 个场景 |
| 9 | MINOR | `keep_floor=3` 无数据依据（新闻门控实测被数据从 5 打到 2） | 保留 3，标注"初始值，待 shadow 数据校准"；shadow 本就绕过它，风险有限 |
| 10 | MINOR | `Total notices` 在 dedup 前后语义未定义 | §6 新增一行：关闭 / shadow 用 `len(df)`（与现状逐字一致），正式模式用过滤后的渲染行数 |

**评审结论**：v1.0 不可直接开工（3 个 BLOCKER 任一都会让 §5.6 的测试 6/测试 4 无法通过，
或让"独立开关"承诺落空）。v1.1 修正后达到可实施状态。

---

## 1. 背景：一个被丢弃的信号源

`akshare_vendor.get_company_announcements()` 当前只渲染**标题 + 类型 + 日期 + 链接**，没有正文。实测（002594.SZ，2026-09-01~09-22，21 条公告）：

```
### 比亚迪:2026年9月18日投资者关系活动记录表
**Type**: 调研活动
**Date**: 2026-09-20
**Link**: https://data.eastmoney.com/notices/detail/002594/AN202609201829667819.html
```

而该公告的**正文**（经东财 API 抓取，1447 字符）包含：

> 2026 上半年...营业收入 **3448 亿元**，归母净利润 **123 亿元**，二季度净利润同比 **+30%**，
> 毛利率达 **18.9%**，创下近一年新高...海外业务占比持续抬升...

同类证据（同批公告）：
- 9/15 记录表：**储能系统出货量全球第一**，覆盖 110+ 国家，日均 2.3 亿公里路况数据
- 9/11 记录表：营收 3448 亿、净利 123 亿、毛利率 18.85%、**现金储备 1674 亿**、研发投入 **289 亿**

**分析师现在看到的是一个空标题，而真实内容全在被丢弃的正文里。** 这不是"新闻太脏需要过滤"，而是"信号被截断"。

### 1.1 但正文的主要问题是冗余，不是噪声

同一批 21 条里，8 条是「投资者关系活动记录表」，关键财务数字高度重叠：

```
9/17: 营收3448亿, 净利123亿, 毛利率18.9%
9/11: 营收3448亿, 净利123亿, 毛利率18.85%   ← 同一组数字
```

因此需要**两个不同的工具**：

| 问题 | 工具 | 理由 |
|------|------|------|
| 8 条 IR 记录互相重复 | **去重**（代码） | 结构性问题，代码比概率模型更合适、更便宜、可复现 |
| 例行的「股东会通知 / 暂停过户登记」 | **Jev 门控** | 语义判断，正是 System One 的价值区 |

---

## 2. 实测数据（决定可行性的硬指标）

| 指标 | 实测值 | 来源 |
|------|--------|------|
| 公告条数 | 21（002594.SZ，9/01~9/22） | akshare `stock_individual_notice_report` |
| art_code 可提取率 | 21/21 | 从 `网址` 列正则 `AN\d+` |
| 正文 API | `np-cnotice-stock.eastmoney.com/api/content/ann` | 返回 `data.notice_content` |
| 抓取成功率 | 21/21（并发 5，3 次重试） | 实测 |
| **抓取耗时** | **44.8s**（并发 5，无缓存） | 实测 |
| 正文总字符 | 38,173（均值 1,817/条） | 实测 |
| 截断 800 后 | 16,800 字符 ≈ 11k tokens | 估算（中文 ~1.5 字符/token） |
| 单次 Jev 成本 | ~$0.0005 | $0.042/Mtok × 11k |
| 数据源列名 | `代码, 名称, 公告标题, 公告类型, 公告日期, 网址` | 实测 |

**45s/票是必须正视的成本** → 缓存是必需项，不是优化项。

---

## 3. 总体设计

```
stock_individual_notice_report (DataFrame: 标题/类型/日期/网址)
    ↓ 提取 art_code → 写入每行 `_art_code`（MINOR-7：只提取一次）
    ↓ 【新】去重（身份键 _art_code → 网址 → 标题+日期；按类型限流）
    ↓   ※ 仅 ann_enabled 且非 shadow；必须在抓取之前（BLOCKER-2/3）
    ↓ 【新】并发抓正文 + 磁盘缓存（仅 ann_enabled 且 fetch_bodies，不依赖 API key）
    ↓ 【新】Jev 门控（语义重要性，config_prefix="jev_ann_gate" 参数化复用）
    ↓
kept 公告 → 渲染标题 + 类型 + 日期 + 链接 + 【正文】
demoted 公告 → 既有标题-only 区（信息不丢失）
```

### 3.1 挂载点

`tradingagents/dataflows/akshare_vendor.py` 的 `get_company_announcements()`（:659），在 DataFrame 拿到之后、markdown 拼装之前。

`get_company_announcements_cninfo()`（:699）**本轮不接**——它的数据形态未验证，且 cninfo 是 fallback 位，优先级低。

### 3.2 失败全开（fail-open）

与新闻门控同构，且**更严格**：抓正文失败 → 退回标题-only 渲染（即现状），绝不抛异常。理由同新闻门控：`route_to_vendor` 会把异常当 vendor 失败触发 fallback 链（`interface.py:1539`），门控/抓取故障绝不能污染这个语义。

### 3.3 Shadow 模式

默认 `jev_ann_gate_shadow=true`：抓正文 + 打分 + 写日志，但**不改变渲染**（仍标题-only）。同时**绕过 keep_floor**（沿用新闻门控 2026-09-22 的修订理由：shadow 的目的是收集数据来校准阈值，跳过就没数据）。

> 注意：shadow 模式下抓正文会产生 45s 延迟但输出不变。若只想收集评分数据不想付延迟，可设 `jev_ann_gate_fetch_bodies=false`（只对标题打分）。

---

## 4. Jev 调用契约

### 4.1 复用现有客户端（v1.1 修订：4 类参数化点）

`tradingagents/llm_clients/typesafe_client.py` 的 `TypeSafeNewsGate` 已支持
`articles: list[dict]` + `context: dict`，公告可直接映射：

| article 字段 | 公告来源 |
|--------------|---------|
| `title` | `公告标题` |
| `body` | 抓取并去标签后的 `notice_content`（送判前截断至 `jev_ann_gate_body_trunc`） |
| `publisher` | 固定 `"公告"` |
| `pub_date` | `公告日期` |
| `link` | `网址` |

v1.0 只提出参数化 `question` / `criteria`，**遗漏了其余三处硬编码**，v1.1 补全：

| # | 硬编码点 | 位置 | 公告侧需要 | 参数化方式 |
|---|---------|------|-----------|-----------|
| 1 | `_QUESTION` / `_CRITERIA_*` 模块常量 | `typesafe_client.py:11-33` | 公告专用措辞（§4.2） | `score_articles(..., question=None, criteria=None)`，`None` → 用新闻默认值 |
| 2 | `_BODY_TRUNC = 500` 模块常量 | `typesafe_client.py:9` | 800 | `score_articles(..., body_trunc=None)`，`None` → 用 `_BODY_TRUNC` |
| 3 | `instructions` 里的子键名 `"article"` | `typesafe_client.py:59` | 保持 `"article"`（见下） | **不参数化** —— 见 §4.3 |
| 4 | `jev_news_gate_timeout` / `jev_news_gate_max_articles` | `typesafe_client.py:40-41` | 独立的 `jev_ann_gate_*` | `TypeSafeNewsGate(config, config_prefix="jev_ann_gate")` |

**关于第 3 点**：v1.0 的 §4.3 自造了 `instructions.announcement` 键名，与已落地代码的
`instructions.article` 不一致。评审决定**统一沿用 `article`** —— 它只是一个"被评判对象"
的结构化容器，对公告同样成立；不为了名字好看去增加一个配置维度。v1.0 §4.3 的
"数据经结构化 object 进入请求体"这一核心要求不受影响。

**门控入口同样需要前缀**（BLOCKER-1 的另一半）：

```python
def apply_news_gate(
    articles: list[dict],
    symbol: str,
    company_name: str,
    date_range: str = "",
    *,
    config_prefix: str = "jev_news_gate",   # ← 新增，新闻路径零变化
    question: str | None = None,
    criteria: dict[str, str] | None = None,
    body_trunc: int | None = None,
) -> tuple[list[int], list[int]] | None:
    config = get_config()
    if not config.get(f"{config_prefix}_enabled") or not articles:
        return None
    shadow = config.get(f"{config_prefix}_shadow", True)
    if not shadow and len(articles) <= config.get(f"{config_prefix}_keep_floor", 5):
        return None
    gate = TypeSafeNewsGate(config, config_prefix=config_prefix)
    scores = gate.score_articles(
        articles, {...}, question=question, criteria=criteria, body_trunc=body_trunc,
    )
    ...
    threshold = config.get(f"{config_prefix}_threshold", 0.5)
```

`jev_api_key_env` 与 `jev_model` **不加前缀**（两个门控共用同一个 key 与模型，本就该共享）。

### 4.2 公告专用问题措辞

```python
_ANN_QUESTION = (
    "Does this announcement contain material, decision-relevant information "
    "about `state.company_name` that an investor should not miss?"
)

_ANN_CRITERIA_TRUE = (
    "Financial results or guidance, major contracts or orders, M&A or "
    "restructuring, equity events (placement, buyback, share incentive, "
    "shareholder increase/decrease), regulatory or litigation outcomes, "
    "production or capacity changes, or substantive management commentary "
    "on business segments"
)

_ANN_CRITERIA_FALSE = (
    "Routine procedural notices: meeting convocation or resolutions with no "
    "new financial substance, record-date or transfer-suspension notices, "
    "investor-relations activity logs that only restate previously disclosed "
    "figures, standard bylaw or board-committee formalities, and other "
    "announcements carrying no new decision-relevant information"
)
```

> `criteria.false` 明确列出「investor-relations activity logs that only restate previously disclosed figures」——这是针对 §1.1 冗余问题在语义层的第二道防线（第一道是 §5.2 的代码去重）。

### 4.3 请求结构（v1.1 修订：键名对齐已落地代码）

```json
{
  "model": "jev-latest",
  "state": {
    "ticker": "002594.SZ",
    "company_name": "比亚迪",
    "date_range": "2026-09-01 to 2026-09-22"
  },
  "questions": {
    "article_0": {
      "type": "noul",
      "instructions": {
        "article": {
          "title": "比亚迪:2026年9月18日投资者关系活动记录表",
          "body": "...(已去标签，截断至 800 字)",
          "publisher": "公告",
          "pub_date": "2026-09-20"
        },
        "question": "<_ANN_QUESTION>"
      },
      "criteria": {"true": "<_ANN_CRITERIA_TRUE>", "false": "<_ANN_CRITERIA_FALSE>"}
    },
    "article_1": { "...": "同上" }
  }
}
```

与新闻门控的差异**只在内容，不在结构**：`instructions.article` 的四个字段、
`criteria` 与 `instructions` 同级、`type: "noul"`、state 传 ticker/company_name/date_range
—— 全部与 `typesafe_client.py:55-75` 的已落地实现一致。

这样保证每条公告的数据**真实进入请求体**（原大方案 §3.4 断链缺陷的修复范式），
且不需要为了公告再造一套请求组装逻辑。

---

## 5. 代码改动清单

### 5.1 新增 `tradingagents/dataflows/announcement_bodies.py`（v1.1：补 strip 契约）

```python
"""东财公告正文抓取 + 磁盘缓存。所有失败路径返回空缺项，由调用方 fail-open。"""

_ANN_API = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
_ART_CODE_RE = re.compile(r"AN\d+")
_TAG_RE = re.compile(r"<[^>]+>")


def extract_art_code(url: str) -> str | None:
    """从公告网址提取 art_code（`AN` + 数字）。失败返回 None。"""


def fetch_bodies(
    art_codes: list[str],
    cache_dir: str,
    *,
    max_workers: int = 5,
    timeout: int = 15,
    retries: int = 3,
) -> dict[str, str]:
    """并发抓正文，命中缓存则直接返回。

    返回 {art_code: 已剥离 HTML 标签的纯文本}。
    缺失 / 失败 / strip 后为空的 code **不出现**在返回值里（MINOR-8），
    由调用方按"无正文"退回标题-only 渲染。
    """
```

要点：
- **缓存**：`<data_cache_dir>/announcement_bodies/<art_code>.txt`，存**已 strip 的纯文本**
  （公告不可变，无 TTL；缓存清洗结果而非原始 HTML，保证命中缓存与首抓行为一致）
- **并发**：`ThreadPoolExecutor(max_workers)`，默认 5（实测 44.8s/21 条）
- **重试**：3 次指数退避（实测东财对连续请求有 SSL 限流）
- **`no_proxy()` 上下文**：必须包住每个请求（AGENTS.md 已知坑，实测不加会 SSLError）
- **User-Agent**：伪装浏览器（实测必要）
- **strip 后为空**：视为该条抓取缺失，不进返回 dict（MINOR-8：渲染退回标题-only，
  送判时该条 body 为空字符串）
- 单条失败不影响其余；全部失败返回空 dict

### 5.2 新增 `tradingagents/dataflows/announcement_dedup.py`（v1.1：身份键前置，BLOCKER-2）

纯函数模块，无网络依赖，便于单测：

```python
def dedup_announcements(
    rows: list[dict],
    *,
    max_per_type: int,
    series_types: tuple[str, ...],
) -> tuple[list[dict], list[dict]]:
    """返回 (kept_rows, dropped_rows)。

    前置契约：调用方须先把 art_code 写进每行的 `_art_code`（见 §5.4），
    使本函数能在**抓取正文之前**完成去重。

    两道规则：
    1. 身份完全相同的，只留公告日期最新的一条（去完全重复）
       身份键按优先级：`_art_code` → `网址` → (`公告标题`, `公告日期`)
    2. 类型属于 series_types（默认「调研活动」）的，按日期倒序只留 max_per_type 条
       （去 IR 记录序列冗余，见 §1.1）
    """
```

> **为什么不用正文哈希**（v1.0 规则 1 的写法，BLOCKER-2）：正文要先抓才存在，而
> §5.4 把 dedup 放在 `fetch_bodies` **之前** —— v1.0 的规则 1 拿不到输入，且其函数
> 签名里根本没有 bodies 参数。东财同一公告的 `art_code` 全局唯一，用它做身份等价
> 且省一次抓取。§1.1 的 8 条 IR 冗余主要靠规则 2（类型序列限流）解决，本来就与
> 正文哈希无关。

### 5.3 修改 `tradingagents/llm_clients/typesafe_client.py`

`score_articles()` 增加三组可选参数，**默认值与行为对新闻侧完全不变**（MAJOR-4）：

| 参数 | 默认 | 消费方 | 目的 |
|------|------|--------|------|
| `question` | `_QUESTION`（新闻措辞） | payload `question` | 公告语体用 §4.2 的 `_ANN_QUESTION` |
| `criteria` | `(news, not_news)`（新闻二分） | payload `criteria` | 公告用「重大/不重大」语义（§4.2） |
| `body_trunc` | `None` → 不截断 | `article["body"]` | 公告正文截断至 `jev_ann_gate_body_trunc`(800) |

**仍不改的**：payload 键名保持 `instructions["article"]`（§4.3，对齐
`typesafe_client.py:55-75` 已落地实现，不自造 `announcement` 键）；`timeout` /
`max_articles` 经 `TypeSafeNewsGate.__init__` 构造传入（§5.5 两套配置键），
`score_articles` 不新增 timeout 形参以避免与 `jev_ann_gate_fetch_timeout` 撞名。

`TypeSafeNewsGate.__init__` 增加 `config_prefix: str = "jev_news_gate"`（BLOCKER-1），
读取的配置键变为 `f"{config_prefix}_timeout"` / `f"{config_prefix}_max_articles"`；
共享键 `jev_api_key_env` / `jev_model` **不加前缀**（§5.5）。

### 5.4 修改 `tradingagents/dataflows/akshare_vendor.py`（`get_company_announcements`）

（v1.1 重写 —— v1.0 版本是 BLOCKER-1/2/3 的集中来源）

```python
    from tradingagents.dataflows import announcement_bodies, announcement_dedup, news_gate
    from tradingagents.llm_clients.typesafe_client import _ANN_QUESTION, _ANN_CRITERIA_*

    rows = [dict(r) for _, r in df.iterrows()]
    # MINOR-7：art_code 每行只提取一次，后续抓取与构稿都只读 `_art_code`
    for r in rows:
        r["_art_code"] = announcement_bodies.extract_art_code(r.get("网址", ""))

    ann_enabled = bool(cfg.get("jev_ann_gate_enabled"))
    shadow = bool(cfg.get("jev_ann_gate_shadow", True))

    # BLOCKER-3：dedup 只在 enabled 时计算
    # BLOCKER-2：身份键已前置，此处无需正文
    #   渲染行集 = 关闭/shadow → 全量 rows（输出与现状逐字一致）
    #             正式模式   → dedup 后
    kept_rows = rows
    dedup_dropped: list[dict] = []
    if ann_enabled:
        dedup_kept, dedup_dropped = announcement_dedup.dedup_announcements(
            rows,
            max_per_type=cfg.get("jev_ann_gate_max_per_type", 3),
            series_types=("调研活动",),
        )
        if not shadow:
            kept_rows = dedup_kept
        # shadow 下 dedup_dropped 只写进 JSONL 观察，不动渲染

    # MAJOR-6：抓取只由 enabled AND fetch_bodies 控制，**不依赖 API key**
    bodies: dict[str, str] = {}
    if ann_enabled and cfg.get("jev_ann_gate_fetch_bodies", True):
        codes = [r["_art_code"] for r in kept_rows if r.get("_art_code")]
        bodies = announcement_bodies.fetch_bodies(
            codes,
            cfg["data_cache_dir"],
            timeout=cfg.get("jev_ann_gate_fetch_timeout", 15),
            max_workers=cfg.get("jev_ann_gate_max_workers", 5),
        )

    articles = [
        {
            "title": r.get("公告标题", ""),
            "body": bodies.get(r.get("_art_code") or "", ""),
            "publisher": "公告",
            "pub_date": str(r.get("公告日期", "")),
            "link": r.get("网址", ""),
        }
        for r in kept_rows
    ]

    gate_result = None
    if ann_enabled:
        # BLOCKER-1：config_prefix 让公告读自己的 jev_ann_gate_* 键
        gate_result = news_gate.apply_news_gate(
            articles, symbol, company_name,
            date_range=f"{start_date} to {end_date}",
            config_prefix="jev_ann_gate",
            question=_ANN_QUESTION,
            criteria={"true": _ANN_CRITERIA_TRUE, "false": _ANN_CRITERIA_FALSE},
            body_trunc=cfg.get("jev_ann_gate_body_trunc", 800),
        )
    # gate_result = None 的全部情况（关闭 / 无 key / 评分失败 / keep_floor / shadow）
    # → 全部按 kept 渲染，与现状一致

    kept_idx, demoted_idx = gate_result if gate_result else (list(range(len(articles))), [])
    demoted_rows = [articles[i] for i in demoted_idx]
    kept_articles = [articles[i] for i in kept_idx]

    # 正文是否渲染：与 gate_result 解耦
    #   shadow 明确不加正文（否则"shadow 只记录"的承诺被打破）
    #   正式模式下即使 gate fail-open（无 key / 评分失败），正文照样交付（MAJOR-6）
    render_bodies = ann_enabled and cfg.get("jev_ann_gate_fetch_bodies", True) and not shadow
```

渲染段（v1.1）：

- `Total notices: {len(kept_rows)}` —— 关闭与 shadow 下 `kept_rows is rows`，等于 `len(df)`，
  与现状逐字一致；正式模式为过滤后的行数（修复 MINOR-10 的语义未定义）
- kept 公告：标题 + 类型 + 日期 + 链接，`render_bodies and body` 时追加正文
  （正文非空才渲染，空则退回纯标题，避免渲染空段落）
- demoted 公告：走标题-only 区，复用新闻门控的 🤖 小节模式，文案改为公告措辞
  （例：`### 🤖 以下 N 条公告经 Jev 门控判定为例行程序性通知…`）
- `dedup_dropped` 在 shadow 模式下随 JSONL 落盘（`would_dedup` 字段），正式模式只影响渲染

### 5.5 修改 `tradingagents/default_config.py`（v1.1 修订：补 2 个键 + 澄清 timeout 语义）

```python
# ── Jev 公告正文增强 + 门控（独立于新闻门控，config_prefix="jev_ann_gate"）──
"jev_ann_gate_enabled": False,        # 总开关（默认关闭）
"jev_ann_gate_shadow": True,          # shadow：抓取+打分+日志，渲染与现状逐字一致
"jev_ann_gate_fetch_bodies": True,    # false = 只对标题打分，省 45s 抓取延迟
"jev_ann_gate_threshold": 0.5,        # noul ≥ 此值保留正文
"jev_ann_gate_keep_floor": 3,         # 正式模式下公告数 ≤ 此值不送判
                                       #   （初始值，待 shadow 数据校准 —— MINOR-9；
                                       #    shadow 本就绕过它，风险有限）
"jev_ann_gate_max_articles": 30,      # 送判上限，超出部分按 kept 处理
                                       #   （MAJOR-5：v1.0 的 §6 引用了它却漏了配置）
"jev_ann_gate_max_per_type": 3,       # 同类公告（如「调研活动」）最多保留条数
"jev_ann_gate_body_trunc": 800,       # 送判正文截断字符数
"jev_ann_gate_timeout": 15,           # **Jev 评分** HTTP 超时（秒）
                                       #   与 jev_news_gate_timeout 同名同义，
                                       #   config_prefix 机制下必须自己有值
"jev_ann_gate_fetch_timeout": 15,     # **正文抓取** HTTP 超时（秒）
                                       #   （v1.1 拆出 —— v1.0 只有 timeout 一个键，
                                       #    两个语义会撞在同一前缀上）
"jev_ann_gate_max_workers": 5,        # 并发抓取数
```

共享键（**不加前缀**，两个门控共用）：`jev_api_key_env`、`jev_model`。

`_ENV_OVERRIDES` 新增对应 `TRADINGAGENTS_JEV_ANN_GATE_*` 映射（11 项）。

### 5.6 新增 `tests/test_announcement_gate.py`（v1.1：9 → 12 场景）

| # | 场景 | 断言 | 覆盖的评审项 |
|---|------|------|-------------|
| 1 | `extract_art_code` 正常/畸形 URL | 正确提取 / 返回 None | — |
| 2 | 缓存命中不发 HTTP | mock `requests.get`，断言零调用 | — |
| 3 | 抓取失败 | 返回空 dict，不抛异常 | — |
| 4 | 身份去重（`_art_code` 相同） | 相同身份的两条只留公告日期最新的一条 | **BLOCKER-2** |
| 5 | 类型序列限流 | 8 条「调研活动」→ 只留 3 条最新 | — |
| 6 | 门控关闭 | **不去重、不抓正文**，渲染与现状逐字一致 | **BLOCKER-3** |
| 7 | shadow 模式 | 抓取+打分+写 JSONL，但渲染与现状**逐字一致**（无正文、无去重、无降级） | **BLOCKER-3** |
| 8 | 正式模式 | kept 含正文，demoted 仅标题 | — |
| 9 | 全部抓取失败 | 退回标题-only，不抛 `NoMarketDataError` | — |
| 10 | **无 API key** | **仍抓取并渲染正文**，不降级，记 warning | **MAJOR-6** |
| 11 | **正文 strip 后为空** | 该条退回标题-only，不渲染空段落 | **MINOR-8** |
| 12 | **`config_prefix` 隔离** | 只开 `jev_ann_gate_enabled` 时新闻门控不执行；只开 `jev_news_gate_enabled` 时公告门控不执行；同时开时两边各读各的 threshold | **BLOCKER-1** |

场景 12 的新闻侧断言同时落在 `tests/test_news_gate.py`（确认默认 `config_prefix`
使新闻路径零变化），公告侧落在本文件。

全部 mock 网络（`requests.get` / `requests.post` + `ak.stock_individual_notice_report`）。

### 5.7 文档同步

`tradingagents/dataflows/AGENTS.md` 边界节 + 根 `AGENTS.md` Data Vendors 段 +
`tradingagents/llm_clients/AGENTS.md`（记录 `config_prefix` / 参数化能力）+ 本 spec
的实施进展注记。

---

## 6. 行为规约（fail-open 全路径，v1.1 重写）

| 条件 | 行为 |
|------|------|
| `jev_ann_gate_enabled=False`（默认） | **完全现状**：不去重、不抓正文、不送判；渲染逐字一致；零开销、零延迟 |
| `shadow=True` | 抓取 + 打分 + 写 JSONL（含 `would_dedup` 观察字段），但**渲染与现状逐字一致**：不去重、不加正文、不降级；**绕过 keep_floor** |
| 未配置 API key | **仍抓正文并渲染**（抓取只由 `enabled AND fetch_bodies` 决定），仅跳过评分与降级，记 warning —— 信号恢复不被 Jev key 绑架（MAJOR-6） |
| `fetch_bodies=False` | 不抓正文（省 45s 延迟），对标题打分与降级照常 |
| 正文 API 超时 / 非 200 / JSON 缺键（单条） | 该条退回标题-only，其余正常 |
| 正文 strip 后为空 | 该条按"无正文"处理：渲染退回标题-only，送判时 body 为空串（MINOR-8） |
| 正文 API 全失败 | 全部退回标题-only，不抛异常、不抛 `NoMarketDataError` |
| 缓存命中 | 不发 HTTP，零延迟 |
| 公告数 ≤ `keep_floor`(3) 且 `shadow=False` | 不送判（省成本）；**去重与正文渲染照常**（它们与评分是两件事） |
| `shadow=True` 且公告数 ≤ `keep_floor` | 照常送判（shadow 绕过 keep_floor） |
| 去重后公告数为 0 | 正常渲染空列表，不抛 `NoMarketDataError` |
| demote 后 kept 为空 | 正常渲染 demoted 标题区，不抛错 |
| 公告数 > `jev_ann_gate_max_articles`(30) | 前 N 条送判，超出部分按 kept 处理（评分补 1.0） |
| **`Total notices` 计数** | 关闭 / shadow = `len(df)`（与现状逐字一致）；正式模式 = 去重后的渲染行数（MINOR-10） |
| `route_to_vendor` 语义保护 | 以上任何一条都**不抛异常**；唯一抛 `NoMarketDataError` 的仍是"akshare 返回空表"这一个原始条件 |

---

## 7. 验证

```bash
uv run python -m pytest tests/test_announcement_gate.py -m unit
uv run python -m pytest -m unit -k "announce or vendor or akshare or news"
uv run python -m pytest -m unit
```

手动 shadow 验证：
```bash
TRADINGAGENTS_JEV_ANN_GATE_ENABLED=true uv run python -m cli.main analyze --tickers 002594.SZ --profile "SenseNova Token Plan"
```
检查：① 渲染是否与现状一致（shadow 不改变输出）② `jev_gate_decisions.jsonl` 是否含公告条目 ③ 抓取的正文是否落盘缓存。

---

## 8. 出界事项（本轮不做）

1. `get_company_announcements_cninfo()`（fallback 位，数据形态未验证）
2. 语义级去重（用 Jev 判断"是否与已选项重复"）——先用代码去重，语义去重留待 v2
3. 新闻门控的 keep_floor 调参（需先积累 shadow 数据）
4. ticker 级 skip / FastThink 路由（原大方案 Phase 1/2）
5. 研报正文抓取（研报源同样是标题+元数据，但东财研报 API 形态不同，需单独验证）

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 45s/票延迟拖慢 pipeline | 磁盘永久缓存（第二次零延迟）+ 并发 5 + 可 `fetch_bodies=false` 降级 |
| 东财反爬/限流 | 3 次指数退避 + `no_proxy()` + 浏览器 UA；失败 fail-open |
| 正文截断 800 字丢失关键信息 | IR 记录的关键财务数据实测在前 400 字内；阈值可配 |
| Jev 对公告语体的校准未验证 | shadow 先行 + 去重兜底 + 降级不删除 |
| token 净增（830 → 约 2,700 渲染 tokens） | 门控过滤 + 去重限流双重收敛；Jev 侧成本仅 $0.0005 |
| 缓存无限增长 | 单条正文 < 2KB，1000 只票 × 21 条 ≈ 40MB；可接受，暂不加清理 |

---

## 10. 与新闻门控的关系

| 维度 | 新闻门控（已落地） | 公告增强+门控（本 spec） |
|------|-------------------|------------------------|
| 解决的问题 | 过滤噪声（榜单/资金流快讯） | **补回被丢弃的信号** + 去冗余 |
| 数据源 | `stock_news_em` | `stock_individual_notice_report` |
| 是否有正文 | 有（但名称过滤已清掉大部分） | 有（但当前完全没渲染） |
| 主要价值 | 注意力质量（有限，实测 23 票只有 1 票触发） | 信息完整性（大，公告含财报/订单/指引） |
| 延迟成本 | ~1s | **~45s（首次）/ 0s（缓存后）** |
| 共享代码 | `news_gate.apply_news_gate` / `TypeSafeNewsGate` | 复用同一套，经 `config_prefix="jev_ann_gate"` + 4 类参数化（§4.1） |

**两者独立开关、可分别启用** —— v1.0 时这句话还不成立（复用方内部只读
`jev_news_gate_*`，见 BLOCKER-1），v1.1 引入 `config_prefix` 后才真正成立：
公告读 `jev_ann_gate_*`，新闻读 `jev_news_gate_*`，`jev_api_key_env` / `jev_model` 共享。

新闻门控的经验（shadow 先行、fail-open、结构化 instructions、绕过 keep_floor）
全部沿用到本 spec。

---

## 11. 审计检查清单（验收标准，v1.1 更新）

**挂载与数据流**
- [ ] 抓取挂载点在 DataFrame 拿到之后、markdown 拼装之前；未改 `interface.py` / analyst 节点
- [ ] 每条公告数据经结构化 **`instructions.article`** 真实进入请求体，无"构建了没传"断链
- [ ] `_art_code` 每行只提取一次，抓取与构稿都读同一个 `_art_code`

**评审修正项回归（3 BLOCKER + 3 MAJOR）**
- [ ] **BLOCKER-1**：只开 `jev_ann_gate_enabled` 时公告门控执行且读 `jev_ann_gate_*`；
      只开 `jev_news_gate_enabled` 时公告门控不执行；新闻侧默认行为零变化
- [ ] **BLOCKER-2**：去重在 `fetch_bodies` **之前**完成，且不依赖正文（身份键：`_art_code` → `网址` → `标题+日期`）
- [ ] **BLOCKER-3**：`enabled=False` 时**既不去重也不抓正文**，渲染与现状逐字一致
- [ ] **MAJOR-4**：`question` / `criteria` / `body_trunc` / `config_prefix` 四类参数化齐全，新闻侧默认值不变
- [ ] **MAJOR-5**：`jev_ann_gate_max_articles` 与 `jev_ann_gate_fetch_timeout` 均存在且 env 覆盖生效
- [ ] **MAJOR-6**：无 API key 时**仍抓取并渲染正文**，仅跳过评分与降级，记 warning

**fail-open 与渲染**
- [ ] §6 行为规约表全部 **15 行**逐条验证
- [ ] 缓存命中不发 HTTP（测试断言零 `requests` 调用）
- [ ] 去重两道规则各自有效（身份重复 / 类型序列限流）
- [ ] 正文 strip 后为空 → 该条退回标题-only，不渲染空段落
- [ ] 全部抓取失败时退回标题-only，不抛 `NoMarketDataError`
- [ ] shadow 模式渲染与现状**逐字节一致**（无正文、无去重、无降级）
- [ ] 正式模式：kept 含正文，demoted 走 🤖 标题-only 区
- [ ] `Total notices` 关闭/shadow 为 `len(df)`，正式模式为去重后行数

**可观测性**
- [ ] JSONL 在 shadow 和正式模式都落盘，含 ts / symbol / shadow / 每篇 title+score+kept；
      shadow 下另含 `would_dedup` 观察字段
- [ ] `TRADINGAGENTS_JEV_ANN_GATE_*` env 覆盖生效（11 项）；默认关闭时零行为变化

**测试**
- [ ] 单测不依赖网络（mock `requests.get` / `requests.post` + `ak.stock_individual_notice_report`），**12 场景全过**
- [ ] 新闻门控侧 `config_prefix` 回归测试落在 `tests/test_news_gate.py` 且全过
- [ ] `uv run python -m pytest -m unit` 全量通过
- [ ] `uv run ruff check .` 与 `uv run mypy tradingagents cli --ignore-missing-imports` 干净
- [ ] 覆盖率 `--fail-under=90` 通过；新增两个模块各自 ≥90%
