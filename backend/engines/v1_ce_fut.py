import pandas as pd
from datetime import timedelta
from typing import Dict, Any, Tuple
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base import (
    get_strike_data,
    load_expiry,
    load_base2,
    load_bhavcopy,
    build_intervals,
    compute_analytics,
    build_pivot,
)

# ==========================================================
# V1 STRATEGY — CE SELL + FUTURE BUY
# Exact replication of original main1() script logic
# ==========================================================
def run_v1(params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:

    index_name  = params.get("index", "NIFTY")
    call_percent = params.get("call_sell_position", 0)   # same default as script (0 = ATM)

    # ----------------------------------------------------------
    # Load data
    # ----------------------------------------------------------
    spot_df    = get_strike_data(index_name, params["from_date"], params["to_date"])
    weekly_exp = load_expiry(index_name, "weekly")
    monthly_exp = load_expiry(index_name, "monthly")
    base2      = load_base2()

    # ----------------------------------------------------------
    # Base2 Filter  (identical to script lines 398-401)
    # ----------------------------------------------------------
    mask = pd.Series(False, index=spot_df.index)
    for _, row in base2.iterrows():
        mask |= (spot_df['Date'] >= row['Start']) & (spot_df['Date'] <= row['End'])
    spot_df = spot_df[mask].reset_index(drop=True)

    trades = []

    # ----------------------------------------------------------
    # WEEKLY EXPIRY LOOP  (script lines 414-416)
    # ----------------------------------------------------------
    for w in range(len(weekly_exp)):

        prev_expiry = weekly_exp.iloc[w]['Previous Expiry']
        curr_expiry = weekly_exp.iloc[w]['Current Expiry']

        # -------------------------------------------------------
        # FUTURE EXPIRY DEFAULT — iloc[0] of monthly >= curr_expiry
        # EXACT COPY of script lines 418-427
        # -------------------------------------------------------
        curr_monthly_expiry = monthly_exp[
            monthly_exp['Current Expiry'] >= curr_expiry
        ].sort_values(by='Current Expiry').reset_index(drop=True)

        if curr_monthly_expiry.empty:
            continue

        # Script uses iloc[0] — nearest monthly expiry >= curr weekly expiry
        fut_expiry = curr_monthly_expiry.iloc[0]['Current Expiry']

        # -------------------------------------------------------
        # Filter window  (script lines 429-435)
        # -------------------------------------------------------
        filtered_data = spot_df[
            (spot_df['Date'] >= prev_expiry) &
            (spot_df['Date'] <= curr_expiry)
        ].sort_values(by='Date').reset_index(drop=True)

        if len(filtered_data) < 2:
            continue

        # -------------------------------------------------------
        # Re-entry / intervals  (script lines 437-479)
        # -------------------------------------------------------
        spot_adjustment_type = params.get("spot_adjustment_type", 0)
        spot_adjustment      = params.get("spot_adjustment", 1)

        intervals = []

        if spot_adjustment_type != 0:
            fd = filtered_data.copy(deep=True)
            fd['ReEntry']    = False
            fd['Entry_Price'] = None
            fd['Pct_Chg']    = None
            entry_price = None

            for t in range(len(fd)):
                if t == 0:
                    entry_price = fd.iloc[t]['Close']
                    fd.at[t, 'Entry_Price'] = entry_price
                else:
                    if entry_price is not None:
                        roc = 100 * (fd.iloc[t]['Close'] - entry_price) / entry_price
                        fd.at[t, 'Entry_Price'] = entry_price
                        fd.at[t, 'Pct_Chg']    = roc

                    if (
                        (spot_adjustment_type == 3 and abs(roc) >= spot_adjustment) or
                        (spot_adjustment_type == 2 and roc <= -spot_adjustment) or
                        (spot_adjustment_type == 1 and roc >= spot_adjustment)
                    ):
                        fd.at[t, 'ReEntry'] = True
                        entry_price = fd.iloc[t]['Close']

            reentry = fd[fd['ReEntry'] == True]
            if len(reentry) > 0:
                start = filtered_data.iloc[0]['Date']
                for d in reentry['Date']:
                    intervals.append((start, d))
                    start = d
                if start != filtered_data.iloc[-1]['Date']:
                    intervals.append((start, filtered_data.iloc[-1]['Date']))
            else:
                intervals.append((filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date']))
        else:
            intervals.append((filtered_data.iloc[0]['Date'], filtered_data.iloc[-1]['Date']))

        if not intervals:
            continue

        interval_df = pd.DataFrame(intervals, columns=['From', 'To'])

        # -------------------------------------------------------
        # TRADE LOOP over intervals  (script lines 481-666)
        # -------------------------------------------------------
        for i in range(len(interval_df)):

            fromDate = interval_df.iloc[i]['From']
            toDate   = interval_df.iloc[i]['To']

            if fromDate == toDate:
                continue

            # ---------------------------------------------------
            # BASE START OVERRIDE — EXACT COPY of script lines 489-494
            # When fromDate is the start of a base2 range, override fut_expiry
            # to iloc[1] of monthly > fromDate  (NOT >=, strictly >)
            # ---------------------------------------------------
            is_base_start = (base2['Start'] == fromDate).any()
            if is_base_start:
                override_df = monthly_exp[
                    monthly_exp['Current Expiry'] > fromDate   # strictly >
                ].reset_index(drop=True)
                fut_expiry = override_df.iloc[1]['Current Expiry']   # iloc[1]

            # ---------------------------------------------------
            # Entry / Exit spots  (script lines 498-508)
            # ---------------------------------------------------
            entry_row = filtered_data[filtered_data['Date'] == fromDate]
            exit_row  = filtered_data[filtered_data['Date'] == toDate]

            if entry_row.empty:
                continue

            entry_spot = entry_row.iloc[0]['Close']
            exit_spot  = exit_row.iloc[0]['Close'] if not exit_row.empty else None

            # ---------------------------------------------------
            # STRIKE FORMULA — EXACT COPY of script line 510
            # Uses Python built-in round() (banker's rounding), /100 *100
            # ---------------------------------------------------
            call_strike = round((entry_spot * (1 + call_percent / 100)) / 100) * 100

            # ---------------------------------------------------
            # Load bhavcopy  (script lines 512-550)
            # ---------------------------------------------------
            bhav_entry = load_bhavcopy(fromDate.strftime('%Y-%m-%d'))
            bhav_exit  = load_bhavcopy(toDate.strftime('%Y-%m-%d'))

            if bhav_entry is None or bhav_exit is None:
                continue

            # ---------------------------------------------------
            # CALL ENTRY DATA  (script lines 552-588)
            # StrikePrice % 100 == 0  (original data has 100-point strikes)
            # ---------------------------------------------------
            if call_percent >= 0:
                call_entry_data = bhav_entry[
                    (bhav_entry['Instrument'] == "OPTIDX") &
                    (bhav_entry['Symbol']     == index_name) &
                    (bhav_entry['OptionType'] == "CE") &
                    (
                        (bhav_entry['ExpiryDate'] == curr_expiry) |
                        (bhav_entry['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                        (bhav_entry['ExpiryDate'] == curr_expiry + timedelta(days=1))
                    ) &
                    (bhav_entry['StrikePrice'] >= call_strike) &
                    (bhav_entry['TurnOver']    >  0) &
                    (bhav_entry['StrikePrice'] % 100 == 0)
                ].sort_values(by='StrikePrice', ascending=True).reset_index(drop=True)
            else:
                call_entry_data = bhav_entry[
                    (bhav_entry['Instrument'] == "OPTIDX") &
                    (bhav_entry['Symbol']     == index_name) &
                    (bhav_entry['OptionType'] == "CE") &
                    (
                        (bhav_entry['ExpiryDate'] == curr_expiry) |
                        (bhav_entry['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                        (bhav_entry['ExpiryDate'] == curr_expiry + timedelta(days=1))
                    ) &
                    (bhav_entry['StrikePrice'] <= call_strike) &
                    (bhav_entry['TurnOver']    >  0) &
                    (bhav_entry['StrikePrice'] % 100 == 0)
                ].sort_values(by='StrikePrice', ascending=False).reset_index(drop=True)

            if call_entry_data.empty:
                continue

            call_strike         = call_entry_data.iloc[0]['StrikePrice']
            call_entry_price    = call_entry_data.iloc[0]['Close']
            call_entry_turnover = call_entry_data.iloc[0]['TurnOver']

            # ---------------------------------------------------
            # CALL EXIT DATA  (script lines 591-613)
            # ---------------------------------------------------
            call_exit_data = bhav_exit[
                (bhav_exit['Instrument'] == "OPTIDX") &
                (bhav_exit['Symbol']     == index_name) &
                (bhav_exit['OptionType'] == "CE") &
                (
                    (bhav_exit['ExpiryDate'] == curr_expiry) |
                    (bhav_exit['ExpiryDate'] == curr_expiry - timedelta(days=1)) |
                    (bhav_exit['ExpiryDate'] == curr_expiry + timedelta(days=1))
                ) &
                (bhav_exit['StrikePrice'] == call_strike)
            ]

            if call_exit_data.empty:
                continue

            call_exit_price    = call_exit_data.iloc[0]['Close']
            call_exit_turnover = call_exit_data.iloc[0]['TurnOver']
            call_pnl           = round(call_entry_price - call_exit_price, 2)

            # ---------------------------------------------------
            # FUTURE ENTRY / EXIT  (script lines 618-640)
            # ---------------------------------------------------
            fut_entry_data = bhav_entry[
                (bhav_entry['Instrument']         == "FUTIDX") &
                (bhav_entry['Symbol']             == index_name) &
                (bhav_entry['ExpiryDate'].dt.month == fut_expiry.month) &
                (bhav_entry['ExpiryDate'].dt.year  == fut_expiry.year)
            ]
            fut_exit_data = bhav_exit[
                (bhav_exit['Instrument']          == "FUTIDX") &
                (bhav_exit['Symbol']              == index_name) &
                (bhav_exit['ExpiryDate'].dt.month == fut_expiry.month) &
                (bhav_exit['ExpiryDate'].dt.year  == fut_expiry.year)
            ]

            if fut_entry_data.empty or fut_exit_data.empty:
                continue

            fut_entry_price = fut_entry_data.iloc[0]['Close']
            fut_exit_price  = fut_exit_data.iloc[0]['Close']
            fut_pnl         = round(fut_exit_price - fut_entry_price, 2)

            net_pnl = round(call_pnl + fut_pnl, 2)

            # ---------------------------------------------------
            # Append trade  (matches script lines 644-666)
            # ---------------------------------------------------
            trades.append({
                "Entry Date":           fromDate,
                "Exit Date":            toDate,
                "Entry Spot":           entry_spot,
                "Exit Spot":            exit_spot,
                "Spot P&L":             round(exit_spot - entry_spot, 2) if exit_spot else None,
                "Future Expiry":        fut_expiry,
                "Future EntryPrice":    fut_entry_price,
                "Future ExitPrice":     fut_exit_price,
                "Future P&L":           fut_pnl,
                "Call Expiry":          curr_expiry,
                "Call Strike":          call_strike,
                "Call EntryPrice":      call_entry_price,
                "Call Entry Turnover":  call_entry_turnover,
                "Call ExitPrice":       call_exit_price,
                "Call Exit Turnover":   call_exit_turnover,
                "Call P&L":             call_pnl,
                "Net P&L":              net_pnl,
            })

    # ----------------------------------------------------------
    # Final output
    # ----------------------------------------------------------
    if not trades:
        return pd.DataFrame(), {}, {}

    df = pd.DataFrame(trades)
    df, summary = compute_analytics(df)
    pivot = build_pivot(df, 'Call Expiry')

    return df, summary, pivot


# ----------------------------------------------------------
# Wrapper functions
# ----------------------------------------------------------
def run_v1_main1(params):
    params['expiry_window'] = 'weekly_expiry'
    return run_v1(params)

def run_v1_main2(params):
    params['expiry_window'] = 'weekly_t1'
    return run_v1(params)

def run_v1_main3(params):
    params['expiry_window'] = 'weekly_t2'
    return run_v1(params)

def run_v1_main4(params):
    params['expiry_window'] = 'monthly_expiry'
    return run_v1(params)

def run_v1_main5(params):
    params['expiry_window'] = 'monthly_t1'
    return run_v1(params)