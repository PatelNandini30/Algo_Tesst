import os
import glob
import datetime
import sys
import time
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta

def createLogFile(symbol, reason, call_expiry=None, put_expiry=None, fut_expiry=None, _from=None, _to=None):
    global logFile
    logFile.append({
        'Symbol' : symbol,
        "Call Expiry" : call_expiry,
        "Put Expiry" : put_expiry,
        "Future Expiry" : fut_expiry,
        "Reason" : reason,
        "From" : _from,
        "To" : _to
    })
