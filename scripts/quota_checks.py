"""每日校准使用量上限测试：管理密钥设定/调整上限（按上游证书 × UTC 日期）、
签发核额（单张/批次/复核批准）、批内合并计数、超限拒绝与错误详情、
撤销不退还已用次数、UTC 日期口径、批次幂等重放不重复计数、
并发调整与并发签发不超额、既有接口契约保持。

直接压 HTTP 接口（urllib，无第三方依赖）。服务需已启动。
用法: python3 scripts/quota_checks.py [base_url] [management_key] [entry_key] [reviewer_key]

依赖：DELIVERY_MANAGEMENT_KEY 默认为 test-admin-key（与其余 *_checks.py 相同，
直接对按默认/测试配置启动的服务运行即可）；录入/复核凭据默认与
review_checks.py 相同（caltrace-entry-li / caltrace-review-zhao）。
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
MK = sys.argv[2] if len(sys.argv) > 2 else "test-admin-key"
ENTRY = sys.argv[3] if len(sys.argv) > 3 else "caltrace-entry-li"
REV = sys.argv[4] if len(sys.argv) > 4 else "caltrace-review-zhao"
results = []


def call(method, path, body=None, key=None, entry=None, reviewer=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Management-Key"] = key
    if entry is not None:
        headers["X-Entry-Key"] = entry
    if reviewer is not None:
        headers["X-Reviewer-Key"] = reviewer
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" :: {detail}"))


def err_code(body):
    return body.get("error", {}).get("code")


TZ = timezone(timedelta(hours=8))
_seq = [0]
_seq_lock = threading.Lock()

# 两个配额日（UTC）：D1=2026-03-05，D2=2026-03-06
D1, D2 = "2026-03-05", "2026-03-06"
# 校准时刻：D1 当天（UTC 正午）；D1 边界（+08:00 的 3-06 07:30 = UTC 3-05 23:30）；
# D2 起点（+08:00 的 3-06 08:00 = UTC 3-06 00:00）
CAT_D1 = "2026-03-05T12:00:00+00:00"
CAT_D1_EDGE = "2026-03-06T07:30:00+08:00"
CAT_D2 = "2026-03-06T08:00:00+08:00"

REF_VF = "2026-01-01T00:00:00+08:00"
REF_VT = "2027-06-01T00:00:00+08:00"
CAL_VF = "2026-03-01T00:00:00+08:00"
CAL_VT = "2027-03-01T00:00:00+08:00"


def nxt(prefix):
    with _seq_lock:
        _seq[0] += 1
        return f"{prefix}-{_seq[0]}"


def ref(no):
    return call("POST", "/api/v1/reference-certificates", {
        "cert_no": no, "device_id": "DEV-" + no, "device_name": "基准" + no,
        "valid_from": REF_VF, "valid_to": REF_VT, "calibrated_at": REF_VF})


def cal(no, parent, cat=CAT_D1):
    return call("POST", "/api/v1/certificates", {
        "cert_no": no, "device_id": "DEV-" + no, "device_name": "仪表" + no,
        "valid_from": CAL_VF, "valid_to": CAL_VT, "calibrated_at": cat,
        "conclusion": "PASS", "parent_cert_no": parent})


def set_limit(no, day, limit, key=MK):
    return call("POST", "/api/v1/daily-usage-limits",
                {"cert_no": no, "usage_date": day, "limit": limit}, key=key)


def get_limit(no, day, key=MK):
    return call("GET", f"/api/v1/daily-usage-limits/{no}/{day}", key=key)


def batch(key_, items):
    return call("POST", "/api/v1/certificate-batches",
                {"batch_key": key_, "certificates": items})


def cal_item(no, parent, cat=CAT_D1):
    return {"kind": "CALIBRATION", "cert_no": no, "device_id": "DEV-" + no,
            "device_name": "仪表" + no, "valid_from": CAL_VF, "valid_to": CAL_VT,
            "calibrated_at": cat, "conclusion": "PASS", "parent_cert_no": parent}


def ref_item(no):
    return {"kind": "REFERENCE", "cert_no": no, "device_id": "DEV-" + no,
            "device_name": "基准" + no, "valid_from": REF_VF, "valid_to": REF_VT,
            "calibrated_at": REF_VF}


# ---------------------------------------------------------------------------
print("== 0. 前置基准证书 ==")
refs = {}
for tag in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"):
    r = nxt("R-" + tag)
    s, _ = ref(r)
    check(f"[{tag}] 基准 201", s == 201, f"got {s}")
    refs[tag] = r

# ---------------------------------------------------------------------------
print("== A. 管理接口：密钥 / 证书存在性 / 非负上限 ==")
s, e = call("POST", "/api/v1/daily-usage-limits",
            {"cert_no": refs["Q1"], "usage_date": D1, "limit": 3})
check("缺少管理密钥头 401", s == 401 and err_code(e) == "invalid_management_key", f"{s} {err_code(e)}")
s, e = set_limit(refs["Q1"], D1, 3, key="wrong-key")
check("错误管理密钥 401", s == 401 and err_code(e) == "invalid_management_key", f"{s} {err_code(e)}")
s, e = call("GET", f"/api/v1/daily-usage-limits/{refs['Q1']}/{D1}")
check("查询缺少密钥 401", s == 401 and err_code(e) == "invalid_management_key", f"{s} {err_code(e)}")
s, e = set_limit("CERT-NOPE-xyz", D1, 3)
check("设定：证书不存在 404", s == 404 and err_code(e) == "cert_not_found", f"{s} {err_code(e)}")
s, e = get_limit("CERT-NOPE-xyz", D1)
check("查询：证书不存在 404", s == 404 and err_code(e) == "cert_not_found", f"{s} {err_code(e)}")
s, _ = set_limit(refs["Q1"], D1, -1)
check("负数上限 422（结构校验）", s == 422, f"got {s}")

# ---------------------------------------------------------------------------
print("== B. 未配置上限：沿用原签发规则，已用次数照常累计 ==")
s, b = get_limit(refs["Q1"], D1)
check("未配置键查询：limit=null used=0",
      s == 200 and b["limit"] is None and b["used"] == 0, f"{s} {b}")
c1, c2, c3 = nxt("C-Q1-a"), nxt("C-Q1-a"), nxt("C-Q1-a")
for c in (c1, c2, c3):
    s, _ = cal(c, refs["Q1"])
    check(f"未配置上限签发 {c} 201", s == 201, f"got {s}")
s, b = get_limit(refs["Q1"], D1)
check("未配置键已用 3 次", s == 200 and b["limit"] is None and b["used"] == 3, f"{s} {b}")

# ---------------------------------------------------------------------------
print("== C. 设定上限：成功返回上限与已用次数；超限拒绝并指出上游证书和日期 ==")
s, b = set_limit(refs["Q1"], D1, 4)
check("设定上限 4 → 200 且返回 limit=4 used=3",
      s == 200 and b["limit"] == 4 and b["used"] == 3 and b["usage_date"] == D1
      and b.get("updated_at"), f"{s} {b}")
c4 = nxt("C-Q1-a")
s, _ = cal(c4, refs["Q1"])
check("第 4 张签发 201（达到上限）", s == 201, f"got {s}")
c5 = nxt("C-Q1-a")
s, e = cal(c5, refs["Q1"])
check("超限签发 422 daily_usage_limit_exceeded",
      s == 422 and err_code(e) == "daily_usage_limit_exceeded", f"{s} {err_code(e)}")
d = e.get("error", {}).get("details", {})
check("错误详情指出上游证书与 UTC 日期",
      d.get("parent_cert_no") == refs["Q1"] and d.get("usage_date") == D1
      and d.get("limit") == 4 and d.get("used") == 4 and d.get("cert_no") == c5,
      json.dumps(d, ensure_ascii=False))
s, b = get_limit(refs["Q1"], D1)
check("被拒签发不计数：used 仍为 4", s == 200 and b["used"] == 4, f"{s} {b}")

# ---------------------------------------------------------------------------
print("== D. UTC 日期口径：按 calibrated_at 的 UTC 日期计 ==")
s, e = cal(nxt("C-Q1-b"), refs["Q1"], cat=CAT_D1_EDGE)
check("+08:00 的 3-06 07:30 仍属 UTC 3-05 → 超限 422",
      s == 422 and err_code(e) == "daily_usage_limit_exceeded", f"{s} {err_code(e)}")
c_d2 = nxt("C-Q1-b")
s, _ = cal(c_d2, refs["Q1"], cat=CAT_D2)
check("UTC 3-06 00:00 属于新的一天 → 201", s == 201, f"got {s}")
s, b = get_limit(refs["Q1"], D2)
check("D2 键独立计数：limit=null used=1",
      s == 200 and b["limit"] is None and b["used"] == 1, f"{s} {b}")

# ---------------------------------------------------------------------------
print("== E. 调整上限：低于已用拒绝；调高放行 ==")
s, e = set_limit(refs["Q1"], D1, 2)
check("调到已用之下 409 usage_limit_below_used",
      s == 409 and err_code(e) == "usage_limit_below_used", f"{s} {err_code(e)}")
d = e.get("error", {}).get("details", {})
check("拒绝详情给出已用与拟设上限",
      d.get("used") == 4 and d.get("requested_limit") == 2
      and d.get("cert_no") == refs["Q1"] and d.get("usage_date") == D1,
      json.dumps(d, ensure_ascii=False))
s, b = set_limit(refs["Q1"], D1, 4)
check("调到等于已用 200（used=4 不超过 4）", s == 200 and b["limit"] == 4 and b["used"] == 4, f"{s} {b}")
s, e = cal(nxt("C-Q1-c"), refs["Q1"])
check("等于已用时仍超限 422", s == 422 and err_code(e) == "daily_usage_limit_exceeded", f"{s} {err_code(e)}")
s, b = set_limit(refs["Q1"], D1, 5)
check("调高到 5 → 200", s == 200 and b["limit"] == 5, f"{s} {b}")
c5b = nxt("C-Q1-c")
s, _ = cal(c5b, refs["Q1"])
check("调高后签发 201（used=5）", s == 201, f"got {s}")
s, e = cal(nxt("C-Q1-c"), refs["Q1"])
check("再次超限 422", s == 422 and err_code(e) == "daily_usage_limit_exceeded", f"{s} {err_code(e)}")

# ---------------------------------------------------------------------------
print("== F. 上限 0：当日不再放行 ==")
s, b = set_limit(refs["Q2"], D1, 0)
check("设定上限 0 → 200 used=0", s == 200 and b["limit"] == 0 and b["used"] == 0, f"{s} {b}")
s, e = cal(nxt("C-Q2-a"), refs["Q2"])
check("上限 0 签发 422", s == 422 and err_code(e) == "daily_usage_limit_exceeded", f"{s} {err_code(e)}")
d = e.get("error", {}).get("details", {})
check("上限 0 详情 limit=0 used=0", d.get("limit") == 0 and d.get("used") == 0, json.dumps(d))

# ---------------------------------------------------------------------------
print("== G. 撤销/失效不退还已用次数 ==")
s, _ = call("POST", f"/api/v1/certificates/{c5b}/revoke", {"reason": "复核超差，撤销"})
check("撤销已签发证书 200", s == 200, f"got {s}")
s, b = get_limit(refs["Q1"], D1)
check("撤销后已用次数不退还（used 仍为 5）", s == 200 and b["used"] == 5, f"{s} {b}")
s, e = cal(nxt("C-Q1-d"), refs["Q1"])
check("撤销后仍按原计数超限 422", s == 422 and err_code(e) == "daily_usage_limit_exceeded", f"{s} {err_code(e)}")

# ---------------------------------------------------------------------------
print("== H. 批次：批内多张合并计数、整批不落库、幂等重放不重复计数 ==")
s, b = set_limit(refs["Q3"], D1, 2)
check("设定批次键上限 2", s == 200 and b["used"] == 0, f"{s} {b}")
bk1, bc1, bc2 = nxt("HQ1"), nxt("C-Q3-a"), nxt("C-Q3-a")
s, b1 = batch(bk1, [cal_item(bc1, refs["Q3"]), cal_item(bc2, refs["Q3"])])
check("批次 2 张合并计数 ≤ 上限 → 201", s == 201 and b1.get("count") == 2, f"{s} {b1}")
s, b = get_limit(refs["Q3"], D1)
check("批次落库后 used=2", s == 200 and b["used"] == 2, f"{s} {b}")

bk2, bc3 = nxt("HQ2"), nxt("C-Q3-b")
s, e = batch(bk2, [cal_item(bc3, refs["Q3"])])
check("超限批次 422 daily_usage_limit_exceeded",
      s == 422 and err_code(e) == "daily_usage_limit_exceeded", f"{s} {err_code(e)}")
d = e.get("error", {}).get("details", {})
check("批次超限详情：批内编号 + 上游证书 + 日期",
      d.get("cert_no") == bc3 and d.get("parent_cert_no") == refs["Q3"]
      and d.get("usage_date") == D1 and d.get("limit") == 2 and d.get("used") == 2,
      json.dumps(d, ensure_ascii=False))
s, _ = call("GET", f"/api/v1/certificate-batches/{bk2}")
check("超限批次整批不落库（批次 404）", s == 404, f"got {s}")
s, _ = call("GET", f"/api/v1/certificates/{bc3}")
check("超限批次整批不落库（证书 404）", s == 404, f"got {s}")
s, b = get_limit(refs["Q3"], D1)
check("超限批次不计数（used 仍为 2）", s == 200 and b["used"] == 2, f"{s} {b}")

bk3 = nxt("HQ3")
items3 = [cal_item(nxt("C-Q3-c"), refs["Q3"]) for _ in range(3)]
s, e = batch(bk3, items3)
check("批次 3 张合并计数超上限 → 422 整批拒绝",
      s == 422 and err_code(e) == "daily_usage_limit_exceeded", f"{s} {err_code(e)}")
d = e.get("error", {}).get("details", {})
check("合并计数详情 requested=3", d.get("requested") == 3, json.dumps(d))
s, _ = call("GET", f"/api/v1/certificates/{items3[0]['cert_no']}")
check("合并超限整批不落库", s == 404, f"got {s}")

s, b1r = batch(bk1, [cal_item(bc1, refs["Q3"]), cal_item(bc2, refs["Q3"])])
check("同键同请求重放 200 且结果同首次",
      s == 200 and b1r.get("issued_at") == b1.get("issued_at")
      and b1r.get("count") == 2, f"{s}")
s, b = get_limit(refs["Q3"], D1)
check("幂等重放不重复计数（used 仍为 2）", s == 200 and b["used"] == 2, f"{s} {b}")

s, _ = set_limit(refs["Q3"], D1, 5)
s, b3 = batch(bk3, items3)
check("调高上限后原批次键可重新签发 201", s == 201 and b3.get("count") == 3, f"{s}")
s, b = get_limit(refs["Q3"], D1)
check("重新签发后 used=5", s == 200 and b["used"] == 5, f"{s} {b}")

# 多键批次：同一批次命中两个不同日期键，分别计数
s, _ = set_limit(refs["Q4"], D1, 1)
s, _ = set_limit(refs["Q4"], D2, 1)
bk4, m1, m2 = nxt("HQ4"), nxt("C-Q4-a"), nxt("C-Q4-a")
s, _ = batch(bk4, [cal_item(m1, refs["Q4"], cat=CAT_D1), cal_item(m2, refs["Q4"], cat=CAT_D2)])
check("多键批次（D1+D2 各 1 张）201", s == 201, f"got {s}")
s, b = get_limit(refs["Q4"], D1)
check("多键批次 D1 used=1", s == 200 and b["used"] == 1, f"{s} {b}")
s, b = get_limit(refs["Q4"], D2)
check("多键批次 D2 used=1", s == 200 and b["used"] == 1, f"{s} {b}")

# ---------------------------------------------------------------------------
print("== I. 批内引用：按直接上游证书计一次 ==")
bk5, r8, c8a, c8b = nxt("HQ5"), nxt("R-Q8"), nxt("C-Q8-a"), nxt("C-Q8-b")
s, _ = batch(bk5, [ref_item(r8), cal_item(c8a, r8), cal_item(c8b, c8a)])
check("批内引用批次 201", s == 201, f"got {s}")
s, b = get_limit(r8, D1)
check("直接上游为基准：计 1 次", s == 200 and b["used"] == 1, f"{s} {b}")
s, b = get_limit(c8a, D1)
check("直接上游为批内普通证书：计 1 次", s == 200 and b["used"] == 1, f"{s} {b}")

# ---------------------------------------------------------------------------
print("== J. 复核批准：批准时核额，超限申请保持待审 ==")
s, _ = set_limit(refs["Q5"], D1, 1)
app1, cr1 = nxt("RVQ1"), nxt("C-Q5-a")
s, b = call("POST", "/api/v1/certificate-review-requests", {
    "application_no": app1, "cert_no": cr1, "device_id": "DEV-" + cr1,
    "valid_from": CAL_VF, "valid_to": CAL_VT, "calibrated_at": CAT_D1,
    "conclusion": "PASS", "parent_cert_no": refs["Q5"]}, entry=ENTRY)
check("提交复核申请 201 PENDING", s == 201 and b.get("status") == "PENDING", f"{s} {b}")
s, b = call("POST", f"/api/v1/certificate-review-requests/{app1}/decision",
            {"decision": "APPROVE"}, reviewer=REV)
check("批准 201 且真正签发", s == 201 and b.get("status") == "APPROVED"
      and b.get("certificate", {}).get("cert_no") == cr1, f"{s} {b}")
s, b = get_limit(refs["Q5"], D1)
check("批准签发计入用量 used=1", s == 200 and b["used"] == 1, f"{s} {b}")

app2, cr2 = nxt("RVQ2"), nxt("C-Q5-b")
s, _ = call("POST", "/api/v1/certificate-review-requests", {
    "application_no": app2, "cert_no": cr2, "device_id": "DEV-" + cr2,
    "valid_from": CAL_VF, "valid_to": CAL_VT, "calibrated_at": CAT_D1,
    "conclusion": "PASS", "parent_cert_no": refs["Q5"]}, entry=ENTRY)
check("提交第二份申请 201", s == 201, f"got {s}")
s, e = call("POST", f"/api/v1/certificate-review-requests/{app2}/decision",
            {"decision": "APPROVE"}, reviewer=REV)
check("超限批准 422 daily_usage_limit_exceeded",
      s == 422 and err_code(e) == "daily_usage_limit_exceeded", f"{s} {err_code(e)}")
d = e.get("error", {}).get("details", {})
check("批准超限详情指出上游证书与日期",
      d.get("parent_cert_no") == refs["Q5"] and d.get("usage_date") == D1
      and d.get("cert_no") == cr2, json.dumps(d, ensure_ascii=False))
s, b = call("GET", f"/api/v1/certificate-review-requests/{app2}", entry=ENTRY)
check("超限后申请保持待审 PENDING", s == 200 and b.get("status") == "PENDING", f"{s} {b}")
s, _ = call("GET", f"/api/v1/certificates/{cr2}")
check("超限批准不产生证书 404", s == 404, f"got {s}")
s, _ = set_limit(refs["Q5"], D1, 2)
s, b = call("POST", f"/api/v1/certificate-review-requests/{app2}/decision",
            {"decision": "APPROVE"}, reviewer=REV)
check("调高上限后原申请可再批准 201", s == 201 and b.get("status") == "APPROVED", f"{s} {b}")
s, b = get_limit(refs["Q5"], D1)
check("再次批准后 used=2", s == 200 and b["used"] == 2, f"{s} {b}")

# ---------------------------------------------------------------------------
print("== K. 并发：10 路并发签发不超上限；调整与签发并发不超额 ==")
s, _ = set_limit(refs["Q6"], D1, 5)
statuses, codes = [], []
lock = threading.Lock()


def issue_one(tag):
    s, e = cal(nxt("C-Q6-a"), refs["Q6"])
    with lock:
        statuses.append(s)
        codes.append(err_code(e))


threads = [threading.Thread(target=issue_one, args=(i,)) for i in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()
ok_n = sum(1 for s in statuses if s == 201)
exceed_n = sum(1 for s, c in zip(statuses, codes)
               if s == 422 and c == "daily_usage_limit_exceeded")
check("10 路并发恰 5 张 201、5 张超限 422",
      ok_n == 5 and exceed_n == 5, f"201={ok_n} 超限={exceed_n} 全部={list(zip(statuses, codes))}")
s, b = get_limit(refs["Q6"], D1)
check("并发后 used=5 等于上限（未超额）", s == 200 and b["used"] == 5, f"{s} {b}")

# 调整与签发并发：先签 2 张（used=2），上限 5；4 路调到 2、4 路再签发
r7 = refs["Q7"]
for _ in range(2):
    s, _ = cal(nxt("C-Q7-a"), r7)
    check("预先签发 201", s == 201, f"got {s}")
s, _ = set_limit(r7, D1, 5)
check("预设上限 5", s == 200, f"got {s}")
adj_res, iss_res = [], []


def adjust_one():
    s, e = set_limit(r7, D1, 2)
    with lock:
        adj_res.append((s, err_code(e)))


def issue_one7():
    s, e = cal(nxt("C-Q7-b"), r7)
    with lock:
        iss_res.append((s, err_code(e)))


threads = ([threading.Thread(target=adjust_one) for _ in range(4)]
           + [threading.Thread(target=issue_one7) for _ in range(4)])
for t in threads:
    t.start()
for t in threads:
    t.join()
check("调整结果均合法（200 或 409 usage_limit_below_used）",
      all(s == 200 or (s == 409 and c == "usage_limit_below_used") for s, c in adj_res),
      str(adj_res))
check("签发结果均合法（201 或 422 daily_usage_limit_exceeded）",
      all(s == 201 or (s == 422 and c == "daily_usage_limit_exceeded") for s, c in iss_res),
      str(iss_res))
s, b = get_limit(r7, D1)
check("并发调整与签发不超额：最终 used ≤ limit",
      s == 200 and b["limit"] in (2, 5) and 2 <= b["used"] <= b["limit"],
      f"{s} {b}")

# ---------------------------------------------------------------------------
print("== L. 既有契约保持 ==")
s, b = call("GET", "/")
eps = b.get("endpoints", [])
check("根索引包含新端点",
      s == 200
      and "POST /api/v1/daily-usage-limits" in eps
      and "GET  /api/v1/daily-usage-limits/{cert_no}/{usage_date}" in eps,
      f"{s} {eps}")
s, b = call("GET", f"/api/v1/certificates/{c1}")
check("证书详情查询契约不变", s == 200 and b.get("cert_no") == c1, f"{s}")
s, b = call("GET", f"/api/v1/certificates/{c8b}/chain")
check("追溯链查询契约不变", s == 200 and b.get("length") == 3, f"{s}")

# ---------------------------------------------------------------------------
failed = [r for r in results if not r[1]]
print(f"\n合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
if failed:
    for name, _, detail in failed:
        print(f"  FAIL: {name} :: {detail}")
    sys.exit(1)
print("全部通过")
