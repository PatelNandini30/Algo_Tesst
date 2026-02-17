# UI Redesign Summary - Match Reference Layout

## Current vs Reference Layout

### Reference Layout Structure:
```
┌─────────────────────────────────────────────────────────────┐
│ Top Bar: Title + Strategy Completeness Progress (42%)       │
├──────────────────────────┬──────────────────────────────────┤
│ LEFT (2/3 width)         │ RIGHT (1/3 width)                │
│                          │                                  │
│ • Asset Symbol Search    │ ENTRY PARAMETERS                 │
│ • Strategy Legs          │ • Execution Type (buttons)       │
│   - Add New Leg button   │ • Expiry Cycle dropdown          │
│   - Leg cards            │ • Entry Buffer                   │
│                          │                                  │
│                          │ BACKTEST HORIZON                 │
│                          │ • Starting Date                  │
│                          │ • Final Date                     │
│                          │                                  │
│                          │ RUN SIMULATION button            │
└──────────────────────────┴──────────────────────────────────┘
```

### Key Changes Needed:

1. **Top Bar**
   - Title: "Customizable Quant Strategy Dashboard"
   - Subtitle: "Engineered for professional options backtesting..."
   - Progress bar showing "Strategy Completeness: 42%"

2. **Left Column (Strategy Legs)**
   - Asset Symbol Search box at top
   - "Strategy Legs" section with "Add New Leg" button (dark/black)
   - Empty state: "No active legs configured" with icon
   - Preset buttons: "BULL PUT SPREAD", "IRON BUTTERFLY"

3. **Right Column (Entry Parameters)**
   - Clean card with sections:
     - ENTRY PARAMETERS
       * Execution Type: INTRADAY | SWING | POSITIONAL (buttons)
       * Expiry Cycle: Weekly Options (dropdown)
       * Entry Buffer: 2 MARKET DAYS TO EXPIRY
     - BACKTEST HORIZON
       * Starting Date: JAN 01, 2020
       * Final Date: DEC 31, 2023
     - RUN SIMULATION button (full width, light gray)

4. **Remove**
   - Risk Management section (not needed)
   - Old header with Save/Load buttons
   - Separate DTE Settings card

## Color Scheme:
- Background: Light gray (#F9FAFB)
- Cards: White with subtle shadow
- Primary buttons: Dark gray/black (#1F2937)
- Secondary buttons: Light gray
- Text: Gray scale
- Accent: Blue for active states
