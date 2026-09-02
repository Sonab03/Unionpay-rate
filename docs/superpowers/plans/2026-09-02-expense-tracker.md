# 日元消费汇总 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `/expenses` 页面，让用户按历史银联汇率记录并汇总日元消费，记录保存在浏览器且支持 JSON 存档导入导出。

**Architecture:** FastAPI 新增历史汇率查询接口并复用现有文件缓存；浏览器用 localStorage 保存消费记录。计算、校验和存档合并放在独立 JavaScript 纯函数文件，页面控制器只负责 DOM、网络和存储副作用。

**Tech Stack:** Python 3.12、FastAPI、Jinja2、requests、Python unittest、原生 HTML/CSS/JavaScript、浏览器 localStorage。

**Spec:** `docs/superpowers/specs/2026-09-02-expense-tracker-design.md`

## Global Constraints

- 消费记录和备注只能保存在浏览器，不发送到服务器。
- localStorage 键固定为 `unionpay-expenses-v1`，存档 `schemaVersion` 固定为 `1`。
- 历史汇率最多向前寻找 14 天，只能在银联明确无数据时继续回溯。
- JSON 文件最大 5 MiB，最多 10,000 条记录。
- 保持 `/`、`/refresh`、现有缓存格式和深浅色主题兼容。
- 不增加数据库、账号系统、前端框架或运行时依赖。

---

### Task 1: 指定日期历史汇率服务

**Files:**
- Create: `tests/test_historical_rates.py`
- Modify: `unionpay.py`

**Interfaces:**
- Consumes: `fetch_rate_for_day(day)`、`DEFAULT_DATA_DIR`、现有原子 JSON 写入和汇率校验。
- Produces: `get_rate_for_date(requested_day, *, data_dir=None, fetcher=None) -> dict | None`，返回字段 `requestedDate`、`rateDate`、`rate`、`source`。

- [ ] **Step 1: 写精确缓存命中和节假日回溯的失败测试**

```python
class HistoricalRateTests(unittest.TestCase):
    def test_exact_cached_day_avoids_upstream(self):
        # 写入 data/history/2026-08-28.json
        result = get_rate_for_date(
            date(2026, 8, 28),
            data_dir=data_dir,
            fetcher=lambda _day: self.fail("cache hit must not fetch"),
        )
        self.assertEqual("2026-08-28", result["requestedDate"])
        self.assertEqual("2026-08-28", result["rateDate"])

    def test_missing_days_fall_back_to_nearest_previous_rate(self):
        seen = []
        def fetcher(day):
            seen.append(day.isoformat())
            return RATE if day == date(2026, 8, 28) else None
        result = get_rate_for_date(
            date(2026, 8, 30), data_dir=data_dir, fetcher=fetcher
        )
        self.assertEqual(
            ["2026-08-30", "2026-08-29", "2026-08-28"], seen
        )
        self.assertEqual("2026-08-28", result["rateDate"])
```

- [ ] **Step 2: 运行测试确认因接口不存在而失败**

Run: `/home/ubuntu/projects/unionpay-rate/.venv/bin/python -m unittest tests.test_historical_rates -v`

Expected: FAIL/ERROR，`unionpay` 没有 `get_rate_for_date`。

- [ ] **Step 3: 实现缓存、14 日回溯、映射和文件锁**

```python
HISTORICAL_LOOKUP_LOCK_FILE = ".historical-rate.lock"
DATE_LOOKUP_DIR = "date-lookups"
HISTORICAL_LOOKBACK_DAYS = 14

def get_rate_for_date(requested_day, *, data_dir=None, fetcher=None):
    data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    fetcher = fetcher or fetch_rate_for_day
    with _historical_rate_lock(data_dir):
        mapped = _read_date_lookup(data_dir, requested_day)
        if mapped is not None:
            return mapped
        for offset in range(HISTORICAL_LOOKBACK_DAYS):
            candidate = requested_day - timedelta(days=offset)
            rate = _read_history_rate(data_dir, candidate)
            if rate is None:
                rate = fetcher(candidate)
                if _valid_rate(rate):
                    _persist_history_rate(data_dir, rate)
            if _valid_rate(rate):
                result = _historical_response(requested_day, rate)
                _persist_date_lookup(data_dir, requested_day, result)
                return result
        return None
```

锁内必须再次读取映射。`requests.RequestException` 不捕获为“无数据”，直接传给 API 层。映射保存到 `data/date-lookups/YYYY-MM-DD.json`，历史汇率继续保存到 `data/history/YYYY-MM-DD.json`。

- [ ] **Step 4: 增加边界和并发测试**

覆盖：第 14 天命中、第 15 天不访问；上游异常立即停止；成功后再次查询使用映射；两个并发查询只执行一轮 fetch。使用临时目录，不写生产 `data/`。

- [ ] **Step 5: 运行历史汇率测试和完整测试**

Run: `/home/ubuntu/projects/unionpay-rate/.venv/bin/python -m unittest tests.test_historical_rates -v`

Run: `/home/ubuntu/projects/unionpay-rate/.venv/bin/python -m unittest discover -s tests -v`

Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add unionpay.py tests/test_historical_rates.py
git commit -m "feat: add historical rate lookup"
```

### Task 2: 消费页面和历史汇率 API

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app.py`
- Create: `templates/expenses.html`
- Create: `static/expense_tracker_core.js`
- Create: `static/expenses.js`

**Interfaces:**
- Consumes: `get_rate_for_date(requested_day)` from Task 1。
- Produces: `GET /expenses` HTML 页面；`GET /api/rate?date=YYYY-MM-DD` JSON 接口；`/static/*` 静态文件。

- [ ] **Step 1: 写路由失败测试**

```python
def test_expenses_page_renders_tracker_shell(self):
    response = app.expenses(make_request(path="/expenses"))
    html = response.body.decode("utf-8")
    self.assertIn("日元消费汇总", html)
    self.assertIn('id="expenseForm"', html)
    self.assertIn('src="/static/expense_tracker_core.js"', html)

def test_rate_api_returns_requested_and_actual_dates(self):
    with patch.object(app, "get_rate_for_date", return_value=RATE_RESPONSE):
        result = app.rate_for_date("2026-08-30")
    self.assertEqual("2026-08-28", result["rateDate"])
```

另写无效日期、未来日期、无结果 404、`requests.RequestException` 转 502 的测试，检查 `HTTPException.status_code`。

- [ ] **Step 2: 运行路由测试确认失败**

Run: `/home/ubuntu/projects/unionpay-rate/.venv/bin/python -m unittest tests.test_app -v`

Expected: FAIL/ERROR，缺少 `expenses`、`rate_for_date` 和模板。

- [ ] **Step 3: 实现最小路由和静态文件挂载**

```python
from datetime import datetime
from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles
import requests

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/expenses")
def expenses(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="expenses.html",
        context={"app_version": APP_VERSION},
    )

@app.get("/api/rate")
def rate_for_date(date: str):
    try:
        requested_day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError as error:
        raise HTTPException(400, "日期格式无效") from error
    if requested_day > datetime.now(JST).date():
        raise HTTPException(400, "不能查询未来日期")
    try:
        result = get_rate_for_date(requested_day)
    except requests.RequestException as error:
        raise HTTPException(502, "暂时无法取得银联汇率") from error
    if result is None:
        raise HTTPException(404, "14 天内没有可用汇率")
    return result
```

创建只含标题、表单容器、汇总容器、明细容器、存档按钮和脚本引用的最小 `expenses.html`；创建空的两个静态脚本，使页面可加载。

- [ ] **Step 4: 运行路由和完整测试**

Run: `/home/ubuntu/projects/unionpay-rate/.venv/bin/python -m unittest tests.test_app -v`

Run: `/home/ubuntu/projects/unionpay-rate/.venv/bin/python -m unittest discover -s tests -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add app.py templates/expenses.html static/expense_tracker_core.js static/expenses.js tests/test_app.py
git commit -m "feat: add expense tracker routes"
```

### Task 3: 浏览器端计算、校验和存档纯函数

**Files:**
- Modify: `static/expense_tracker_core.js`
- Create: `tests/browser/expense_tracker_core.html`

**Interfaces:**
- Produces: `window.ExpenseTrackerCore`，包含 `calculateSummary(records)`、`validateRecord(record)`、`createArchive(records, exportedAt)`、`parseArchive(text)`、`mergeRecords(current, incoming)`。
- Consumes: 无 DOM、网络或 localStorage，所有输入显式传入。

- [ ] **Step 1: 先创建浏览器测试页并确认失败**

测试页加载 `../../static/expense_tracker_core.js`，用手写断言覆盖：

```javascript
const records = [
  { id: "a", purchaseDate: "2026-08-28", jpyAmount: 10000,
    note: "", rateDate: "2026-08-28", rate: 0.04,
    createdAt: "2026-09-01T00:00:00.000Z", updatedAt: "2026-09-01T00:00:00.000Z" },
  { id: "b", purchaseDate: "2026-08-29", jpyAmount: 30000,
    note: "交通", rateDate: "2026-08-29", rate: 0.05,
    createdAt: "2026-09-01T00:00:00.000Z", updatedAt: "2026-09-01T00:00:00.000Z" }
];
assertEqual(calculateSummary(records).totalJPY, 40000);
assertEqual(calculateSummary(records).totalCNY, 1900);
assertEqual(calculateSummary(records).weightedAverageRate, 0.0475);
```

还要覆盖：坏记录被拒绝、备注超过 200 字、未来汇率日期、导出版本、损坏 JSON 原子失败、10,001 条被拒绝、同 ID 选择较新 `updatedAt`、相同时间保留当前记录、重复导入不重复。

用临时 HTTP 服务打开测试页；实现前页面结果必须显示失败。

- [ ] **Step 2: 实现纯函数**

```javascript
(function (global) {
    "use strict";
    const SCHEMA_VERSION = 1;
    const MAX_RECORDS = 10000;

    function calculateSummary(records) {
        const totals = records.reduce((sum, record) => {
            sum.totalJPY += record.jpyAmount;
            sum.totalCNY += record.jpyAmount * record.rate;
            return sum;
        }, { totalJPY: 0, totalCNY: 0 });
        totals.weightedAverageRate = totals.totalJPY > 0
            ? totals.totalCNY / totals.totalJPY : null;
        totals.averagePer10000JPY = totals.weightedAverageRate === null
            ? null : totals.weightedAverageRate * 10000;
        return totals;
    }

    global.ExpenseTrackerCore = {
        calculateSummary,
        validateRecord,
        createArchive,
        parseArchive,
        mergeRecords
    };
})(window);
```

`parseArchive` 先验证完整输入并返回新数组，不接触 localStorage。`mergeRecords` 创建新数组，不修改输入；相同 ID 仅在导入记录 `updatedAt` 更晚时替换。

- [ ] **Step 3: 在真实浏览器运行测试页**

启动临时静态服务：

```bash
cd /tmp/unionpay-rate-expenses
python3 -m http.server 8002
```

打开 `http://192.168.31.81:8002/tests/browser/expense_tracker_core.html`，确认页面报告全部断言通过且 `data-failures="0"`。

- [ ] **Step 4: 提交**

```bash
git add static/expense_tracker_core.js tests/browser/expense_tracker_core.html
git commit -m "feat: add expense calculation and archive core"
```

### Task 4: 本地记录、表单、明细和汇总交互

**Files:**
- Modify: `templates/expenses.html`
- Modify: `static/expenses.js`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: `window.ExpenseTrackerCore` from Task 3；`GET /api/rate?date=...` from Task 2。
- Produces: localStorage CRUD、汇总渲染、编辑删除和错误反馈。

- [ ] **Step 1: 写消费页面行为契约失败测试**

渲染 `/expenses` 后检查页面包含固定 ID：`expenseForm`、`purchaseDate`、`jpyAmount`、`note`、`summary`、`records`、`formMessage`、`cancelEdit`，并引用两个脚本。检查 `static/expenses.js` 的 HTTP 响应由真实临时服务验证，而不是在 Python 中模拟 DOM。

- [ ] **Step 2: 实现页面结构和响应式样式**

表单使用原生 `date`、`number`、`text` 输入。汇总用四个卡片值。明细在宽屏使用表格、窄屏使用记录卡片。复制现有主题变量和三态主题按钮逻辑，确保自动、浅色、深色三种模式都覆盖页面、输入框、按钮和明细。

- [ ] **Step 3: 实现控制器副作用**

```javascript
const STORAGE_KEY = "unionpay-expenses-v1";
let records = loadRecords();
let editingId = null;

async function handleSubmit(event) {
    event.preventDefault();
    const input = readForm();
    const existing = records.find(record => record.id === editingId);
    const rateInfo = existing && existing.purchaseDate === input.purchaseDate
        ? { rateDate: existing.rateDate, rate: existing.rate }
        : await fetchRate(input.purchaseDate);
    const record = buildRecord(input, rateInfo, existing);
    persistRecords(upsert(records, record));
    resetForm();
    render();
}
```

保存前验证日期、金额和备注。网络请求期间禁用提交按钮。失败时不改变 `records` 或 localStorage，保留表单。localStorage 解析失败时显示错误并停止自动写入。

- [ ] **Step 4: 用临时 FastAPI 服务验证真实页面**

启动：

```bash
cd /tmp/unionpay-rate-expenses
/home/ubuntu/projects/unionpay-rate/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8001
```

浏览器验证：空状态；新增一笔；刷新后仍存在；新增第二笔后加权汇总正确；编辑备注不请求新汇率；修改日期更新汇率；删除更新汇总；浅色、深色、自动主题；390px 宽度没有横向溢出。

- [ ] **Step 5: 运行完整 Python 测试**

Run: `/home/ubuntu/projects/unionpay-rate/.venv/bin/python -m unittest discover -s tests -v`

Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add templates/expenses.html static/expenses.js tests/test_app.py
git commit -m "feat: add local expense tracking UI"
```

### Task 5: JSON 下载与加载交互

**Files:**
- Modify: `templates/expenses.html`
- Modify: `static/expenses.js`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: `createArchive`、`parseArchive`、`mergeRecords` from Task 3。
- Produces: `downloadArchive()` 和 `loadArchive(file)` 用户操作。

- [ ] **Step 1: 写存档控件失败测试**

渲染页面并检查 `downloadArchive` 按钮、只接受 `.json,application/json` 的 `archiveFile`、`loadArchive` 按钮和 `archiveMessage` 状态区域。

- [ ] **Step 2: 实现下载**

```javascript
function downloadArchive() {
    const archive = Core.createArchive(records, new Date().toISOString());
    const blob = new Blob([JSON.stringify(archive, null, 2)], {
        type: "application/json"
    });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `unionpay-expenses-${todayInJapan()}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
}
```

- [ ] **Step 3: 实现原子加载与合并**

先检查 `file.size <= 5 * 1024 * 1024`，读取文本并调用 `parseArchive`。只有解析和全部验证成功后才调用 `mergeRecords` 并执行一次 localStorage 写入。写入失败时保留内存中的旧 `records`。成功后清空文件输入、重新渲染并显示新增/更新/忽略数量。

- [ ] **Step 4: 浏览器验证存档往返**

创建两条记录并下载；删除本地记录；加载存档恢复；再次加载同一文件不重复；加载损坏 JSON 时现有记录不变；加载超过大小限制文件时显示错误。

- [ ] **Step 5: 运行完整测试并提交**

Run: `/home/ubuntu/projects/unionpay-rate/.venv/bin/python -m unittest discover -s tests -v`

Expected: 全部 PASS。

```bash
git add templates/expenses.html static/expenses.js tests/test_app.py
git commit -m "feat: add expense archive import and export"
```

### Task 6: 首页入口、版本和发布验证

**Files:**
- Modify: `templates/index.html`
- Modify: `app.py`
- Modify: `tests/test_app.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `/expenses` from Task 2。
- Produces: 首页“消费汇总”入口和最终版本号。

- [ ] **Step 1: 写首页入口和版本失败测试**

```python
def test_home_links_to_expense_tracker(self):
    html = app.home(make_request()).body.decode("utf-8")
    self.assertIn('href="/expenses"', html)
    self.assertIn("消费汇总", html)
    self.assertIn("v1.2.0", html)
```

- [ ] **Step 2: 加入口、版本和 README 说明**

将 `APP_VERSION` 设为 `1.2.0`。首页在换算卡片下方增加低干扰链接。README 说明 `/expenses`、记录只保存在浏览器、JSON 备份恢复和历史日期回溯规则。

- [ ] **Step 3: 完成前新鲜验证**

Run: `/home/ubuntu/projects/unionpay-rate/.venv/bin/python -m unittest discover -s tests -v`

Run: `git diff --check main..HEAD`

用临时服务检查 `/`、`/expenses`、`/api/rate` 均返回正确状态；运行浏览器核心测试页；完成手机宽度、主题、CRUD、加权汇总和 JSON 往返检查。

- [ ] **Step 4: 提交**

```bash
git add app.py templates/index.html tests/test_app.py README.md
git commit -m "feat: publish expense tracker"
```

- [ ] **Step 5: 合并和上线**

快进合并功能分支到 `main`，在合并结果上再次运行完整测试。读取 `unionpay-rate` 的精确 MainPID，发送 TERM 让 systemd 自动拉起新进程。

- [ ] **Step 6: 公网验证**

验证：

```text
https://rate.sonab.uk/                    -> 200，包含消费汇总入口和 v1.2.0
https://rate.sonab.uk/expenses            -> 200，包含表单、汇总、明细和存档控件
https://rate.sonab.uk/api/rate?date=...   -> 200 或设计中定义的 4xx/5xx
```

确认 systemd 为 active/running、日志无启动异常、生产 `data/history/` 新缓存可读。不要推送 GitHub，除非用户另行要求。
