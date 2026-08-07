import warnings; warnings.filterwarnings("ignore")
import openpyxl
wb=openpyxl.load_workbook("/app/_f5.xlsx", data_only=True)
ws=wb["Trade Sheet"]; rows=list(ws.iter_rows(values_only=True))
print("nrows:",len(rows))
for i in range(1,min(len(rows),20)):
    r=rows[i]
    # Trade=0, Entry=3, Type=10, Strike=11, EntSpot=6, ExtSpot=7, Net=25, Reason=33
    print("r%d: Tr=%s ent=%s exit=%s Type=%s stk=%s es=%s xs=%s net=%s reason=%s"%(
        i,r[0],str(r[3])[:11],str(r[4])[:11],r[10],r[11],r[6],r[7],r[25],str(r[33])[:24]))
