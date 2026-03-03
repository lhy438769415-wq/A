# MTR V26 Post-Mortem Debate

## Context

    We just ran a historical backtest of the "MTR V25" strategy on 300 A-share stocks.
    
    SYSTEM STATUS:
    - Target Pattern: Major Trend Reversal (Bottom)
    - Data Source: Baostock (Adjusted Daily)
    - Sample Size: 300 stocks (Full History)
    
    RESULTS:
    - Total Signals: 0 (Zero)
    - Win Rate: N/A
    
    CURRENT LOGIC (Strict V25):
    1. Prior Trend: Must have at least 24 of the last 60 bars (40%) with Close < EMA20.
    2. Setup: Double Bottom required (Swing Low 2 >= Swing Low 1 * 0.99).
    3. Signal Bar:
       - Trend Quality: TPI > 0 (Bullish Pressure)
       - Bar Shape: Close > Open (Bull Body)
       - Close Location: Close must be in Top 50% of High-Low Range.
    4. Trigger: High 2 (H2) logic.
    
    OBSERVATION:
    The logic seems theoretically perfect for "Textbook MTRs" but practically sterile in the current A-share market.
    Most stocks are rejected either because:
    A) The "Prior Bear Trend" isn't deep enough (market is sideways/choppy).
    B) The Signal Bar isn't "perfect" (e.g. close is at 45% or small bear body).
    
    QUESTION FOR AL BROOKS:
    "Al, we built the perfect filter based on your books, but it found nothing. 
    In a messy, volatile market like A-shares, which of these 'Perfect MTR' constraints is the most likely culprit for killing valid setups? 
    Please propose a 'Pragmatic V26' adjustment that allows for 'Good Enough' setups without opening the floodgates to garbage."
    

## Response
Excellent question. This is a very common experience when transitioning from textbook study to real-market application. Al Brooks would likely nod in recognition and frame the problem like this:

"The perfect setup exists only in hindsight. Your system is designed to find past winners, not future probabilities. In a noisy, trending, or bull market (even with pullbacks), a 'textbook Major Trend Reversal' is a rare species. You are hunting for a unicorn with a checklist for a horse. The market doesn't care about your rules."

### The Most Likely Culprit: The "Prior Trend" Definition

**Al's Diagnosis:** "You're defining a bear trend **statistically** (40% of closes below EMA20), not **contextually**. In a strong bull market or even a trading range, a stock can have a deep, significant pullback that sets up a beautiful MTR, yet never meet that 40% rule because the prior uptrend was so strong. You are filtering out the best trading range reversals and pullback-buy setups in a bull market—which are the most reliable MTRs."

**Secondary Culprit:** The "Perfect Signal Bar." By requiring a close in the *top 50%* on a bull body, you're demanding the signal bar be a strong breakout. An MTR often starts with hesitation—a doji, a small bull bar closing at 40%, a bear bar that fails to go lower. The strength comes **after** the signal bar confirms.

---

### Proposed: **"Pragmatic V26" Adjustment (Al Brooks-Inspired)**

**Philosophy:** Shift from "Finding Perfect Past Bottoms" to "Identifying Probable Future Success." Focus on **Market Context** and **Bar-by-Bar Price Action**.

**1. PRIOR TREND (The Crucial Fix):**
   *   **Replace the 40% rule** with a **Swing-Based Measure**.
   *   **New Logic:** "A clear, measurable sell-off preceding the setup."
   *   **Implementation:**
       *   Identify the most recent **Major Swing High** (using your own swing logic, e.g., a high with lower highs on both sides).
       *   Measure the drop from that High to the current **Swing Low 1**.
       *   **Requirement:** This drop must be ≥ **1.5x the Average True Range (ATR)** of the last 20 bars. This ensures a meaningful move worthy of a reversal attempt, regardless of how many bars were below an EMA.
   *   *Why it works:* It captures significant pullbacks in any market regime (bull, bear, sideways).

**2. SETUP (Double Bottom Relaxation):**
   *   Keep the Double Bottom but make it **less arithmetic** and more **visual/structural**.
   *   **New Logic:** "Two separate legs down, with the second leg not making a significantly lower low."
   *   **Implementation:**
       *   `Swing Low 2 >= Swing Low 1 * 0.98` (Allows for a 2% penetration, common in false breakouts).
       *   **ADD:** The leg between the two lows should have at least 3-5 bars—it must be a *leg*, not a spike.
   *   *Why it works:* Allows for the common "false breakout" below the first low that traps bears.

**3. SIGNAL BAR & TRIGGER (The Pragmatic Entry):**
   *   **Stop demanding perfection on the signal bar.** Look for **evidence of failure of the bear trend**.
   *   **New Logic for Signal Bar:**
       1.  **Trend Quality:** Keep `TPI > 0` (good).
       2.  **Bar Shape:** **Remove** the `Close > Open` requirement. Instead, require: `(Close > Prior Bar's Close) OR (Low > Prior Bar's Low)`.
       3.  **Close Location:** Change from "Top 50%" to **"Not in the Bottom 25%"** (`Close > Low + (High-Low)*0.25`). This allows for weak bulls still taking control.
   *   **New Trigger (H2 Logic Adjustment):**
       *   The classic H2 is good. But for the **first entry**, consider:
       *   **Enter on a stop** at **one tick above the high of the first reasonable bull bar** that closes above the EMA20 **or** above the midpoint of the recent down leg.
       *   This bar doesn't have to be your "signal bar"; it's the first sign of strength *after* the double bottom forms.
   *   *Why it works:* You enter on confirmation of a change in behavior, not on a pre-defined bar shape.

### V26 Summary - The "Good Enough" MTR Hunter:

1.  **Context:** A preceding swing down >= 1.5x ATR(20).
2.  **Setup:** A double bottom structure (second low within 2% of first, with a multi-bar leg between them).
3.  **Signal:** Price action shows bear failure (TPI>0, higher low or higher close).
4.  **Trigger:** Entry on break above the high of the first clear bull bar that challenges the immediate downtrend line/EMA.

**Al's Final Word:** "You make money by entering when the probability shifts, not when all your boxes are checked. **V25 was a microscope for autopsy. V26 should be a compass for navigation.** Test this. You'll get signals. Your job then is to manage the trade—scale in, use sensible stops, and take profits at logical targets. The setup gets you in the game; your trading rules keep you in it."

Start by backtesting **just the change to the Prior Trend logic (Step 1)**. You will likely find 80% of your new, valid signals come from that single change.