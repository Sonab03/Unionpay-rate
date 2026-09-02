(function () {
    "use strict";

    const STORAGE_KEY = "unionpay-expenses-v1";
    const MAX_FILE_SIZE = 5 * 1024 * 1024;
    const core = window.ExpenseTrackerCore;
    const elements = {
        form: document.getElementById("expenseForm"),
        formTitle: document.getElementById("formTitle"),
        purchaseDate: document.getElementById("purchaseDate"),
        jpyAmount: document.getElementById("jpyAmount"),
        note: document.getElementById("note"),
        saveButton: document.getElementById("saveButton"),
        cancelEdit: document.getElementById("cancelEdit"),
        formMessage: document.getElementById("formMessage"),
        recordList: document.getElementById("recordList"),
        emptyState: document.getElementById("emptyState"),
        totalJPY: document.getElementById("summaryTotalJPY"),
        totalCNY: document.getElementById("summaryTotalCNY"),
        averageRate: document.getElementById("summaryAverageRate"),
        per10000: document.getElementById("summaryPer10000"),
        archiveFile: document.getElementById("archiveFile"),
        downloadArchive: document.getElementById("downloadArchive"),
        loadArchive: document.getElementById("loadArchive"),
        archiveMessage: document.getElementById("archiveMessage"),
        themeToggle: document.getElementById("themeToggle")
    };

    let records = [];
    let editingId = null;
    let storageHealthy = true;

    function japanToday() {
        const parts = new Intl.DateTimeFormat("en-US", {
            timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit"
        }).formatToParts(new Date());
        const value = Object.fromEntries(parts.map(part => [part.type, part.value]));
        return `${value.year}-${value.month}-${value.day}`;
    }

    function formatJPY(value) {
        return value.toLocaleString(undefined, { maximumFractionDigits: 2 }) + " JPY";
    }

    function formatCNY(value) {
        return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " CNY";
    }

    function setMessage(element, message, isError) {
        element.textContent = message || "";
        element.classList.toggle("error", Boolean(isError));
    }

    function loadLocalRecords() {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return [];
        try {
            const data = JSON.parse(raw);
            if (!data || data.schemaVersion !== core.SCHEMA_VERSION) throw new Error("版本不支持");
            return core.mergeRecords([], data.records).records;
        } catch (error) {
            storageHealthy = false;
            setMessage(elements.formMessage, "本地记录无法读取。原数据未被覆盖，请加载有效存档恢复。", true);
            return [];
        }
    }

    function persist(nextRecords) {
        if (!storageHealthy) throw new Error("请先加载有效存档恢复本地数据");
        const payload = { schemaVersion: core.SCHEMA_VERSION, records: nextRecords };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
        records = nextRecords;
    }

    function renderSummary() {
        const summary = core.calculateSummary(records);
        elements.totalJPY.textContent = formatJPY(summary.totalJPY);
        elements.totalCNY.textContent = formatCNY(summary.totalCNY);
        elements.averageRate.textContent = summary.weightedAverageRate === null
            ? "—" : `1 JPY = ${summary.weightedAverageRate.toFixed(6)} CNY`;
        elements.per10000.textContent = summary.averagePer10000JPY === null
            ? "—" : `${summary.averagePer10000JPY.toFixed(3)} CNY`;
    }

    function makeText(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        node.textContent = text;
        return node;
    }

    function renderRecords() {
        elements.recordList.replaceChildren();
        elements.emptyState.hidden = records.length !== 0;
        records.forEach(record => {
            const card = makeText("article", "record", "");
            const main = makeText("div", "record-main", "");
            const details = makeText("div", "", "");
            details.append(makeText("strong", "", record.purchaseDate));
            details.append(makeText("div", "record-meta", `汇率日 ${record.rateDate} · 1 JPY = ${record.rate.toFixed(6)} CNY`));
            if (record.note) details.append(makeText("div", "record-note", record.note));
            const amounts = makeText("div", "record-amount", "");
            amounts.append(makeText("div", "record-jpy", formatJPY(record.jpyAmount)));
            amounts.append(makeText("div", "record-cny", formatCNY(record.jpyAmount * record.rate)));
            main.append(details, amounts);
            const actions = makeText("div", "record-actions", "");
            const edit = makeText("button", "", "编辑");
            edit.type = "button"; edit.dataset.action = "edit"; edit.dataset.id = record.id;
            const remove = makeText("button", "danger", "删除");
            remove.type = "button"; remove.dataset.action = "delete"; remove.dataset.id = record.id;
            actions.append(edit, remove);
            card.append(main, actions);
            elements.recordList.append(card);
        });
    }

    function render() {
        renderSummary();
        renderRecords();
    }

    function resetForm(clearMessage) {
        editingId = null;
        elements.form.reset();
        elements.purchaseDate.value = japanToday();
        elements.formTitle.textContent = "添加消费";
        elements.saveButton.textContent = "查询汇率并保存";
        elements.cancelEdit.hidden = true;
        if (clearMessage) setMessage(elements.formMessage, "", false);
    }

    async function fetchRate(date) {
        const response = await fetch(`/api/rate?date=${encodeURIComponent(date)}`);
        if (!response.ok) {
            let detail = "暂时无法取得该日汇率，请稍后重试";
            try { detail = (await response.json()).detail || detail; } catch (_error) { /* ignore */ }
            throw new Error(detail);
        }
        return response.json();
    }

    async function saveRecord(event) {
        event.preventDefault();
        const purchaseDate = elements.purchaseDate.value;
        const jpyAmount = Number(elements.jpyAmount.value);
        const note = elements.note.value.trim();
        if (!purchaseDate || purchaseDate > japanToday()) {
            setMessage(elements.formMessage, "请选择有效且不晚于今天的日期。", true); return;
        }
        if (!Number.isFinite(jpyAmount) || jpyAmount <= 0) {
            setMessage(elements.formMessage, "请输入大于 0 的日元金额。", true); return;
        }

        elements.saveButton.disabled = true;
        setMessage(elements.formMessage, "正在查询银联历史汇率…", false);
        try {
            const existing = editingId ? records.find(record => record.id === editingId) : null;
            const rateData = existing && existing.purchaseDate === purchaseDate
                ? { rateDate: existing.rateDate, rate: existing.rate }
                : await fetchRate(purchaseDate);
            const now = new Date().toISOString();
            const record = core.validateRecord({
                id: existing ? existing.id : crypto.randomUUID(),
                purchaseDate, jpyAmount, note,
                rateDate: rateData.rateDate, rate: Number(rateData.rate),
                createdAt: existing ? existing.createdAt : now, updatedAt: now
            });
            const next = existing
                ? records.map(item => item.id === existing.id ? record : item)
                : records.concat(record);
            persist(core.mergeRecords([], next).records);
            render();
            resetForm(false);
            setMessage(elements.formMessage, `已保存，使用 ${record.rateDate} 的银联汇率。`, false);
        } catch (error) {
            setMessage(elements.formMessage, error.message || "保存失败，请重试。", true);
        } finally {
            elements.saveButton.disabled = false;
        }
    }

    function startEdit(id) {
        const record = records.find(item => item.id === id);
        if (!record) return;
        editingId = id;
        elements.purchaseDate.value = record.purchaseDate;
        elements.jpyAmount.value = record.jpyAmount;
        elements.note.value = record.note;
        elements.formTitle.textContent = "编辑消费";
        elements.saveButton.textContent = "保存修改";
        elements.cancelEdit.hidden = false;
        setMessage(elements.formMessage, "修改日期时会重新查询对应汇率。", false);
        elements.form.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    function deleteRecord(id) {
        const record = records.find(item => item.id === id);
        if (!record || !window.confirm(`删除 ${record.purchaseDate} 的 ${formatJPY(record.jpyAmount)} 记录？`)) return;
        try {
            persist(records.filter(item => item.id !== id));
            if (editingId === id) resetForm(true);
            render();
        } catch (error) {
            setMessage(elements.formMessage, error.message || "删除失败。", true);
        }
    }

    function downloadArchive() {
        try {
            const archive = core.createArchive(records, new Date().toISOString());
            const blob = new Blob([JSON.stringify(archive, null, 2) + "\n"], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url; link.download = `unionpay-expenses-${japanToday()}.json`;
            document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
            setMessage(elements.archiveMessage, `已下载 ${records.length} 条记录。`, false);
        } catch (error) {
            setMessage(elements.archiveMessage, error.message || "下载失败。", true);
        }
    }

    async function loadArchive() {
        const file = elements.archiveFile.files[0];
        if (!file) { setMessage(elements.archiveMessage, "请先选择一个 JSON 存档。", true); return; }
        if (file.size > MAX_FILE_SIZE) { setMessage(elements.archiveMessage, "存档不能超过 5 MiB。", true); return; }
        elements.loadArchive.disabled = true;
        try {
            const incoming = core.parseArchive(await file.text());
            const merged = core.mergeRecords(records, incoming);
            storageHealthy = true;
            persist(merged.records);
            render();
            setMessage(elements.archiveMessage,
                `加载完成：新增 ${merged.added} 条，更新 ${merged.updated} 条，忽略 ${merged.ignored} 条。`, false);
        } catch (error) {
            setMessage(elements.archiveMessage, error.message || "存档加载失败，现有记录未改变。", true);
        } finally {
            elements.loadArchive.disabled = false;
        }
    }

    function setupTheme() {
        const modes = ["auto", "light", "dark"];
        const labels = { auto: "◐ 自动", light: "☀ 浅色", dark: "☾ 深色" };
        let selected = localStorage.getItem("theme");
        if (!modes.includes(selected)) selected = "auto";
        function apply() {
            if (selected === "auto") document.documentElement.removeAttribute("data-theme");
            else document.documentElement.dataset.theme = selected;
            elements.themeToggle.textContent = labels[selected];
        }
        apply();
        elements.themeToggle.addEventListener("click", () => {
            selected = modes[(modes.indexOf(selected) + 1) % modes.length];
            localStorage.setItem("theme", selected); apply();
        });
    }

    elements.form.addEventListener("submit", saveRecord);
    elements.cancelEdit.addEventListener("click", () => resetForm(true));
    elements.recordList.addEventListener("click", event => {
        const button = event.target.closest("button[data-action]");
        if (!button) return;
        if (button.dataset.action === "edit") startEdit(button.dataset.id);
        if (button.dataset.action === "delete") deleteRecord(button.dataset.id);
    });
    elements.downloadArchive.addEventListener("click", downloadArchive);
    elements.loadArchive.addEventListener("click", loadArchive);

    setupTheme();
    records = loadLocalRecords();
    resetForm(false);
    render();
})();
