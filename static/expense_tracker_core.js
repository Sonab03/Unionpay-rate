(function (global) {
    "use strict";

    const SCHEMA_VERSION = 1;
    const MAX_RECORDS = 10000;
    const MAX_NOTE_LENGTH = 200;

    function isObject(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value);
    }

    function isIsoDate(value) {
        if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
            return false;
        }
        const [year, month, day] = value.split("-").map(Number);
        const parsed = new Date(Date.UTC(year, month - 1, day));
        return parsed.getUTCFullYear() === year
            && parsed.getUTCMonth() === month - 1
            && parsed.getUTCDate() === day;
    }

    function isIsoTimestamp(value) {
        return typeof value === "string"
            && Number.isFinite(Date.parse(value));
    }

    function validateRecord(record) {
        if (!isObject(record)) {
            throw new Error("记录格式无效");
        }
        if (typeof record.id !== "string" || record.id.length === 0
                || record.id.length > 128) {
            throw new Error("记录 ID 无效");
        }
        if (!isIsoDate(record.purchaseDate)) {
            throw new Error("消费日期无效");
        }
        if (!Number.isFinite(record.jpyAmount) || record.jpyAmount <= 0) {
            throw new Error("日元金额无效");
        }
        if (typeof record.note !== "string"
                || record.note.length > MAX_NOTE_LENGTH) {
            throw new Error("备注无效");
        }
        if (!isIsoDate(record.rateDate) || record.rateDate > record.purchaseDate) {
            throw new Error("汇率日期无效");
        }
        if (!Number.isFinite(record.rate) || record.rate <= 0) {
            throw new Error("汇率无效");
        }
        if (!isIsoTimestamp(record.createdAt)
                || !isIsoTimestamp(record.updatedAt)) {
            throw new Error("记录时间无效");
        }

        return {
            id: record.id,
            purchaseDate: record.purchaseDate,
            jpyAmount: record.jpyAmount,
            note: record.note,
            rateDate: record.rateDate,
            rate: record.rate,
            createdAt: record.createdAt,
            updatedAt: record.updatedAt
        };
    }

    function validateRecords(records) {
        if (!Array.isArray(records) || records.length > MAX_RECORDS) {
            throw new Error("记录数量无效");
        }
        const ids = new Set();
        return records.map(record => {
            const valid = validateRecord(record);
            if (ids.has(valid.id)) {
                throw new Error("存档包含重复记录 ID");
            }
            ids.add(valid.id);
            return valid;
        });
    }

    function calculateSummary(records) {
        const validRecords = validateRecords(records);
        const totals = validRecords.reduce((summary, record) => {
            summary.totalJPY += record.jpyAmount;
            summary.totalCNY += record.jpyAmount * record.rate;
            return summary;
        }, { totalJPY: 0, totalCNY: 0 });

        totals.weightedAverageRate = totals.totalJPY > 0
            ? totals.totalCNY / totals.totalJPY
            : null;
        totals.averagePer10000JPY = totals.weightedAverageRate === null
            ? null
            : totals.weightedAverageRate * 10000;
        return totals;
    }

    function createArchive(records, exportedAt) {
        if (!isIsoTimestamp(exportedAt)) {
            throw new Error("导出时间无效");
        }
        return {
            schemaVersion: SCHEMA_VERSION,
            exportedAt,
            records: validateRecords(records)
        };
    }

    function parseArchive(text) {
        if (typeof text !== "string") {
            throw new Error("存档内容无效");
        }
        let archive;
        try {
            archive = JSON.parse(text);
        } catch (error) {
            throw new Error("JSON 格式无效");
        }
        if (!isObject(archive) || archive.schemaVersion !== SCHEMA_VERSION) {
            throw new Error("存档版本不支持");
        }
        if (!isIsoTimestamp(archive.exportedAt)) {
            throw new Error("存档导出时间无效");
        }
        return validateRecords(archive.records);
    }

    function mergeRecords(currentRecords, incomingRecords) {
        const current = validateRecords(currentRecords);
        const incoming = validateRecords(incomingRecords);
        const recordsById = new Map(current.map(record => [record.id, record]));
        let added = 0;
        let updated = 0;
        let ignored = 0;

        incoming.forEach(record => {
            const existing = recordsById.get(record.id);
            if (!existing) {
                recordsById.set(record.id, record);
                added += 1;
            } else if (Date.parse(record.updatedAt) > Date.parse(existing.updatedAt)) {
                recordsById.set(record.id, record);
                updated += 1;
            } else {
                ignored += 1;
            }
        });

        const records = Array.from(recordsById.values()).sort((left, right) =>
            right.purchaseDate.localeCompare(left.purchaseDate)
            || right.createdAt.localeCompare(left.createdAt)
        );
        return { records, added, updated, ignored };
    }

    global.ExpenseTrackerCore = {
        SCHEMA_VERSION,
        MAX_RECORDS,
        calculateSummary,
        validateRecord,
        createArchive,
        parseArchive,
        mergeRecords
    };
})(window);
