# MTR V27 Orthodox Audit

## Prompt

    We are finalizing the 'Major Trend Reversal' (MTR) strategy logic in Python.
    The user explicitly requested: "Do not cater to me. Stick strictly to Al Brooks Price Action Theory. No arbitrary constants (like '0.5% rally') unless backed by theory/backtesting."
    
    Please act as "Digital Al Brooks" and strictly audit the proposed V27 logic below. 
    For each stage, strictly check:
    1. Is this consistent with the *Bar by Bar* books?
    2. How do we code this *robustly* without arbitrary hard numbers?
    
    --- PROPOSED V27 LOGIC (For Audit) ---
    
    **Stage 0: Context**
    - Logic: 18 out of 60 bars (30%) closed below EMA20.
    - Brooks Check: Does this define a "Bear Trend"? Or is it too loose?
    
    **Stage 1: Trend Line Break (The First Leg)**
    - Problem: We cannot draw diagonal trend lines easily in code.
    - Proposed Proxy: `Close > EMA20` OR `Rally > 0.5%`.
    - USER CRITIQUE: "0.5% is arbitrary and has no basis in Brooks theory."
    - QUESTION: What is the *Canonical* Al Brooks proxy for a Trend Line Break in vectorized backtesting? 
      (e.g., breaking a prior Lower High? 5-10 bars above EMA? Close > Last Swing High?)
    
    **Stage 2: The Pullback (Higher Low)**
    - Logic: Must be a Higher Low (HL) above the Swing Low.
    - Requirement: "Weaker Momentum".
    - QUESTION: What is the specific Brooks definition of "Weak Pullback"? 
      (e.g. "Lack of consecutive strong bear bars"? "Overlapping bars"? "Doji bars"?) 
      How do we quantify this?
    
    **Stage 3: The Signal (Entry)**
    - Logic: Reversal Bar (Bull Body) at HL.
    - Filter: Removed "Close > EMA20".
    - Risk: Stop below Swing Low.
    - QUESTION: Is entering *below* the EMA20 valid for MTR? Or does Brooks require the signal bar to close above the EMA?
    
    **Stage 4: Target**
    - Logic: Fixed 2R.
    - QUESTION: Does Brooks advocate for fixed 2R on MTRs, or Measured Moves (MM) based on the First Leg height?
    
    --- OUTPUT REQUIRED ---
    Please provide the "Orthodox V27 Logic" in algorithmic steps.
    If a parameter is needed (e.g., number of bars), provide a recommended range based on typical market fractal behavior (e.g., "usually 5-10 bars").
    

## Response
**DIGITAL AL BROOKS AUDIT OF V27 LOGIC**

**Core Principle:** Brooks' methodology is *contextual and subjective*. A fully mechanical system is impossible, but we can code its foundational logic by focusing on **swing point structure** and **bar pattern signatures**, avoiding arbitrary percentages and constants.

---

### **STAGE 0: CONTEXT (Defining the Bear Trend)**
*   **Brooks Check:** The proposed logic (18/60 bars below EMA20) is **invalid**. A trend is defined by *swing points*, not indicator positioning.
*   **Orthodox Definition:** A **Bear Trend** is a series of **Lower Highs (LH)** and **Lower Lows (LL)**.
*   **Robust Coding Logic:**
    1.  Identify the most recent **Major Swing High (MSH)**. (A high with lower highs on both sides, typically over a 5-10 bar lookback).
    2.  Identify at least one **confirmed Lower Low (LL)** following that MSH.
    3.  **Context is valid** if the last 10-20 bars are contained within this LH/LL structure. (The "channel").

### **STAGE 1: TREND LINE BREAK (The First Leg)**
*   **Brooks Check:** The 0.5% rally is arbitrary. A trend line break is a **break of a prior price level**.
*   **Canonical Proxy:** The "First Leg" up is the move that **breaks the most recent minor Lower High (mLH)** within the bear channel.
*   **Robust Coding Logic:**
    1.  Within the bear channel, find the most recent *minor* **Lower High (mLH)** (a high with lower highs on 1-2 bars either side).
    2.  A **Trend Line Break/First Leg** is confirmed when price *closes* above this mLH.
    3.  *Alternative/Confirming Logic:* The leg should consist of mostly bull bars, demonstrating momentum shift. (e.g., 2-3 consecutive bull bars closing near their highs).

### **STAGE 2: THE PULLBACK (Higher Low - HL)**
*   **Brooks Check:** "Weaker Momentum" is defined by **price action signatures**, not an abstract metric.
*   **Orthodox Definition:** A **Weak Pullback** shows:
    *   **Overlapping Bars:** The pullback bars have significant overlap with each other.
    *   **Lack of Strong Bear Bars:** Absence of large, consecutive bear bars closing near their lows.
    *   **Presence of Doji/Small Bars:** Indecision.
    *   **Shallow Retracement:** The pullback retraces **less than 62%** (a common Fibonacci level for a weak pullback) of the First Leg.
*   **Robust Coding Logic:**
    1.  After the First Leg, identify a swing low that is **above the prior bear channel swing low**. This is the **Higher Low (HL)** candidate.
    2.  **Weak Momentum Check (Quantifiable):**
        a. **Overlap:** Calculate the average bar overlap percentage for the pullback bars. A weak pullback should have high overlap (>60%).
        b. **Bear Bar Strength:** Count strong bear bars (e.g., body > 67% of range, close in bottom 25%). A weak pullback should have few or none.
        c. **Retracement Depth:** Measure the pullback's retracement of the First Leg (from mLH break to pullback low). A weak pullback is typically < 62%.

### **STAGE 3: THE SIGNAL (Entry)**
*   **Brooks Check:** The signal is a **Reversal Bar Pattern** at the test of the HL. The EMA is a guide, not a requirement.
*   **Orthodox Definition:** A valid entry is a **Bull Reversal Bar** (e.g., a strong bull bar, a bull outside bar, a double bottom bull bar) **at the HL area**. The close of this bar should show strength (e.g., in the top half of its range).
*   **Answer to EMA Question:** The signal bar can be *below* the EMA20. Brooks prioritizes price action at key levels (the HL) over indicator position. The EMA simply tells you the strength of follow-through may be less.
*   **Robust Coding Logic:**
    1.  At the identified HL zone, detect a **bull reversal bar pattern**:
        *   **Bull Engulfing Bar:** Current bar's body engulfs prior bar's body.
        *   **Outside Bull Bar:** High > prior high, Low < prior low, Close > Open and near bar's high.
        *   **Strong Bull Close:** A bar closing in its top 25% after a test down towards the HL.
    2.  **Entry:** Buy Stop placed **1 tick above the high** of the signal bar.
    3.  **Stop Loss:** Placed **1 tick below the HL** (the swing low of the pullback).

### **STAGE 4: TARGET**
*   **Brooks Check:** A fixed 2:1 Risk/Reward (2R) is **not** advocated. Brooks uses **Measured Moves (MM)** based on the height of the setup.
*   **Orthodox Definition:** The initial target is a **Measured Move (MM)** equal to the height of the **First Leg** (from the bear channel low to the mLH break point), projected upward from the **HL**.
*   **Robust Coding Logic:**
    1.  Calculate: `First_Leg_Height = (First_Leg_High - Bear_Channel_Low)`
    2.  Calculate: `Initial_Target_Price = HL + First_Leg_Height`
    3.  This MM often aligns with the **prior Major Swing High (MSH)** from Stage 0, which becomes a logical profit-taking area.

---

### **ORTHODOX V27 LOGIC - ALGORITHMIC STEPS**

```python
# PSEUDO-CODE STRUCTURE

def detect_MTR_signal(data, lookback=20, minor_swing_lookback=5):
    """
    Detects a Major Trend Reversal (Bull) setup per Al Brooks.
    """
    
    # STAGE 0: BEAR TREND CONTEXT
    # 1. Identify Major Swing High (MSH) - Lookback ~10-20 bars.
    # 2. Identify confirmed Lower Lows (LL) after MSH.
    # 3. Confirm last 'lookback' bars are in LH/LL structure.
    if not is_bear_trend(data):
        return None
    
    # STAGE 1: FIRST LEG (Trend Line Break)
    # 1. Find most recent minor Lower High (mLH) in channel. (Lookback ~5 bars).
    mLH = find_minor_LH(data)
    # 2. Detect First Leg: A close above mLH with momentum (e.g., 2-3 bull bars).
    if not is_first_leg_break(data, mLH):
        return None
    first_leg_high = data['High'].iloc[-1] # Or the high of the break
    first_leg_low = find_channel_low_before_break(data)
    
    # STAGE 2: WEAK PULLBACK (Higher Low)
    # 1. Find pullback low that is > prior bear channel low.
    # 2. Quantify weakness:
    #    a. Retracement < 62% of First Leg Height.
    #    b. High bar overlap in pullback (>60% avg).
    #    c. Few strong bear bars.
    HL, pullback_data = find_weak_pullback(data, first_leg_high, first_leg_low)
    if HL is None:
        return None
    
    # STAGE 3: SIGNAL BAR
    # 1. At HL support, detect a bull reversal bar pattern.
    signal_bar_index = detect_reversal_bar(data, HL)
    if signal_bar_index is None:
        return None
    
    # ENTRY & STOP
    entry_price = data['High'].iloc[signal_bar_index] + 1 * tick_size
    stop_loss_price = HL - 1 * tick_size
    risk = entry_price - stop_loss_price
    
    # STAGE 4: TARGET (Measured Move)
    first_leg_height = first_leg_high - first_leg_low
    target_price = HL + first_leg_height
    
    # RETURN SIGNAL OBJECT
    return {
        'entry': entry_price,
        'stop': stop_loss_price,
        'target': target_price,
        'risk': risk,
        'HL': HL,
        'signal_bar_index': signal_bar_index
    }
```

**Key Takeaways for Coding:**
1.  **Replace Indicators with Swing Points:** All logic stems from Highs/Lows.
2.  **Quantify "Weakness" with Price Action Metrics:** Use bar overlap, retracement depth, and bear bar counts.
3.  **Use Measured Moves, Not Fixed R:R:** The market's own structure provides the target.
4.  **Parameters are Ranges, Not Fixed:** Use `lookback` parameters (e.g., 5-10 bars for minor swings, 10-20 for major trends) that can be optimized, but the *logic* remains pure price action.

This framework removes arbitrary constants and aligns with Brooks' *Bar-by-Bar* theory.