import warnings; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_f.xlsx", data_only=True)
print("=== RULES ===")
for row in wb["Rules"].iter_rows(values_only=True):
    c=[str(x) for x in row if x not in (None,"")]
    if c: print(" | ".join(c))
