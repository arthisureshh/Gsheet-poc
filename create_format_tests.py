"""
Generate test files for all supported formats.

test_data.xls   — 3 sheets:
                  Sheet1: Bug Report (20 rows, color-coded status cells)
                  Sheet2: Inventory (15 rows)
                  Sheet3: Attendance (10 members x 20 days, color-only cells)

test_data.csv   — Project tracker (30 rows, multiple sprints)
test_data.tsv   — Sales orders (25 rows, multiple regions)
test_noisy.csv  — Employee table with stray text rows + blank rows above/below

Run:
    python create_format_tests.py
"""
import csv
import random
from datetime import date, timedelta

random.seed(42)

NAMES      = ["Alice Chen", "Bob Smith", "Carol White", "David Lee", "Eva Brown",
              "Frank Kim", "Grace Liu", "Henry Park", "Iris Wang", "Jack Zhang"]
STATUSES   = ["Done", "Active", "Overdue", "Blocked", "Review"]
PRIORITIES = ["Critical", "High", "Medium", "Low"]
DEPTS      = ["Engineering", "Sales", "Marketing", "HR", "Finance"]
PRODUCTS   = ["Laptop Pro", "Wireless Mouse", "USB Hub", "Monitor 27\"", "Keyboard Mech",
              "Webcam HD", "Headset BT", "Desk Lamp", "Chair Ergo", "Standing Desk"]
REGIONS    = ["North", "South", "East", "West", "Central"]


# ── test_data.csv — Project Tracker (30 rows) ─────────────────────────────────
with open("test_data.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Task ID", "Task Name", "Assignee", "Status", "Priority",
                "Due Date", "Story Points", "Sprint", "Department"])
    for i in range(30):
        due = date(2025, 1, 1) + timedelta(days=random.randint(0, 120))
        w.writerow([
            f"TASK-{100+i}",
            f"Implement feature {i+1} for module {chr(65 + i % 5)}",
            NAMES[i % len(NAMES)],
            random.choice(STATUSES),
            random.choice(PRIORITIES),
            due.isoformat(),
            random.randint(1, 13),
            f"Sprint {(i // 10) + 1}",
            DEPTS[i % len(DEPTS)],
        ])
print("Created test_data.csv  - 30-row project tracker")


# ── test_data.tsv — Sales Orders (25 rows) ────────────────────────────────────
with open("test_data.tsv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["Order ID", "Sales Rep", "Product", "Region", "Qty",
                "Unit Price", "Total", "Order Date", "Status"])
    for i in range(25):
        qty   = random.randint(1, 50)
        price = round(random.uniform(9.99, 999.99), 2)
        od    = date(2025, 1, 1) + timedelta(days=random.randint(0, 89))
        w.writerow([
            f"ORD-{2000+i}",
            NAMES[i % len(NAMES)],
            random.choice(PRODUCTS),
            random.choice(REGIONS),
            qty,
            price,
            round(qty * price, 2),
            od.isoformat(),
            random.choice(["Shipped", "Pending", "Cancelled", "Delivered"]),
        ])
print("Created test_data.tsv  - 25-row sales orders")


# ── test_noisy.csv — Employee table with stray text + blank rows ──────────────
with open("test_noisy.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Report generated: 2025-04-01", "", "", "", ""])
    w.writerow(["Source: HR System", "", "", "", ""])
    w.writerow([])
    w.writerow(["Employee ID", "Name", "Department", "Score", "Review Date"])
    for i in range(20):
        rev = date(2025, 1, 1) + timedelta(days=random.randint(0, 89))
        w.writerow([
            f"EMP-{1000+i}",
            NAMES[i % len(NAMES)],
            DEPTS[i % len(DEPTS)],
            round(random.uniform(60, 100), 1),
            rev.isoformat(),
        ])
    w.writerow([])
    w.writerow(["Note: scores are normalized", "", "", "", ""])
print("Created test_noisy.csv - 20-row employee table with stray text + blank rows")


# ── test_data.xls — legacy Excel with color-coded cells ──────────────────────
try:
    import xlwt

    # Status color map — mirrors xlsx color sentinel approach
    STATUS_COLORS = {
        "Open":        0x0A,  # red
        "In Progress": 0x34,  # blue
        "Resolved":    0x11,  # green
        "Closed":      0x16,  # grey
    }
    ATTEND_COLORS = {
        True:  0x11,  # green = present  (xlwt palette index)
        False: 0x0A,  # red   = absent
    }

    def color_style(color_idx: int) -> xlwt.XFStyle:
        style = xlwt.XFStyle()
        pattern = xlwt.Pattern()
        pattern.pattern = xlwt.Pattern.SOLID_PATTERN
        pattern.pattern_fore_colour = color_idx
        style.pattern = pattern
        return style

    wb = xlwt.Workbook()

    # ── Sheet 1: Bug Report (20 rows, color-coded Status column) ─────────────
    ws1 = wb.add_sheet("Bug Report")
    bug_headers = ["Bug ID", "Title", "Reporter", "Assignee", "Severity",
                   "Status", "Created Date", "Resolved Date"]
    hdr_style = color_style(0x16)  # grey header
    for col, h in enumerate(bug_headers):
        ws1.write(0, col, h, hdr_style)

    for i in range(20):
        created  = date(2025, 1, 1) + timedelta(days=random.randint(0, 60))
        resolved = (created + timedelta(days=random.randint(1, 30))).isoformat() \
                   if random.random() > 0.3 else ""
        status   = random.choice(list(STATUS_COLORS.keys()))
        ws1.write(i+1, 0, f"BUG-{500+i}")
        ws1.write(i+1, 1, f"Bug description {i+1} in component {chr(65+i%4)}")
        ws1.write(i+1, 2, NAMES[i % len(NAMES)])
        ws1.write(i+1, 3, NAMES[(i+3) % len(NAMES)])
        ws1.write(i+1, 4, random.choice(["Critical", "High", "Medium", "Low"]))
        ws1.write(i+1, 5, status, color_style(STATUS_COLORS[status]))
        ws1.write(i+1, 6, created.isoformat())
        ws1.write(i+1, 7, resolved)

    # ── Sheet 2: Inventory (15 rows) ─────────────────────────────────────────
    ws2 = wb.add_sheet("Inventory")
    inv_headers = ["SKU", "Product", "Category", "Stock", "Reorder Level",
                   "Unit Cost", "Supplier", "Last Restocked"]
    for col, h in enumerate(inv_headers):
        ws2.write(0, col, h, hdr_style)
    CATEGORIES = ["Electronics", "Furniture", "Accessories", "Peripherals"]
    SUPPLIERS  = ["SupplierA", "SupplierB", "SupplierC", "SupplierD"]
    for i in range(15):
        restocked = date(2024, 10, 1) + timedelta(days=random.randint(0, 120))
        stock = random.randint(0, 500)
        # Red background if stock below reorder level
        reorder = random.randint(10, 50)
        stock_style = color_style(0x0A) if stock < reorder else xlwt.XFStyle()
        ws2.write(i+1, 0, f"SKU-{3000+i}")
        ws2.write(i+1, 1, PRODUCTS[i % len(PRODUCTS)])
        ws2.write(i+1, 2, random.choice(CATEGORIES))
        ws2.write(i+1, 3, stock, stock_style)
        ws2.write(i+1, 4, reorder)
        ws2.write(i+1, 5, round(random.uniform(5.0, 999.99), 2))
        ws2.write(i+1, 6, random.choice(SUPPLIERS))
        ws2.write(i+1, 7, restocked.isoformat())

    # ── Sheet 3: Attendance (10 members x 20 days, color-only cells) ─────────
    ws3 = wb.add_sheet("Attendance")
    work_days = []
    d = date(2025, 1, 2)
    while len(work_days) < 20:
        if d.weekday() < 5:
            work_days.append(d)
        d += timedelta(days=1)

    ws3.write(0, 0, "Name", hdr_style)
    for j, day in enumerate(work_days):
        ws3.write(0, j+1, day.strftime("%d-%b"), hdr_style)

    for i, member in enumerate(NAMES):
        ws3.write(i+1, 0, member)
        for j in range(len(work_days)):
            present = random.random() < 0.85
            # Color-only cell — empty string value, color = attendance signal
            ws3.write(i+1, j+1, "", color_style(ATTEND_COLORS[present]))

    wb.save("test_data.xls")
    print("Created test_data.xls  - 3 sheets: Bug Report (color status) + Inventory (color stock) + Attendance (color-only)")

except ImportError:
    print("Skipped test_data.xls  - xlwt not installed: pip install xlwt")


print("\nTest each format:")
print("  test_data.csv   - 1 table, 30 rows, 9 cols")
print("  test_data.tsv   - 1 table, 25 rows, 9 cols")
print("  test_noisy.csv  - 1 table, 20 rows, stray text above + below")
print("  test_data.xls   - 3 sheets: Bug Report + Inventory + Attendance (color cells)")
