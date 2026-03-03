# MTR V27 Recalibration

## Feedback

    CRITICAL FEEDBACK: The user (a Trader) stated our MTR Logic is "Off Track".
    We need to realign the Python logic to match the Canonical MTR definition provided by the user.
    
    USER'S DEFINITION (The Truth):
    1. **Stage 0 (Trend)**: Clear bear trend (TPI > Threshold), creates a Swing Low.
    2. **Stage 1 (Break)**: MUST be a "Trend Line Break" (First Leg), NOT just a "H1 High Break".
       - This leg usually tests or breaks the EMA20.
       - A simple 1-bar high break is insufficient; it needs to be a "Move".
    3. **Stage 2 (Test)**: The pullpack (Higher Low) must show "Weaker Momentum" than Stage 0.
    4. **Stage 3 (Signal)**: The Signal Bar Close does NOT need to be > EMA20.
       - "Waiting for EMA20 close is too late."
       - Entry is on the reversal from the HL.
    5. **Target**: Should be 2R based on Stop Distance, not "Distance to Old High".

    CURRENT LOGIC FLAWS (V26):
    - We use `h1_active` (1-bar high break) as Stage 1. (Too sensitive).
    - We enforce `momentum_gate` (Close > EMA20) for the Signal. (Too strict/late).
    - We don't measure "Momentum Weakening" in Stage 2.
    
    QUESTIONS FOR AL BROOKS AI:
    1. **Quantifying "Trend Line Break"**: Since we don't draw diagonal lines in code, what is the best robust proxy for "Breaking the Bear Trend Line"?
       - Is it "Close > EMA20"? 
       - Is it "N bars overlap"?
       - Is it "K% rally from low"?
    
    2. **Quantifying "Weaker Momentum"**: How do we mathematically verify that Stage 2 (The Pullback) is "weaker" than Stage 0 (The Crash)?
       - Compare Slopes?
       - Compare MACD Histograms?
       - Compare Bear Body Sizes?
       
    3. **The "Early" Signal**: If we drop the "Close > EMA20" requirement, what prevents us from buying a "Lower Low Continuation"? 
       - What constitutes the "Trigger" if not the EMA breach? (e.g. "Good Signal Bar" + "Context"?)
    
    Please provide the "V27 Logic Specs" in pseudocode.
    

## Response
**AL BROOKS AI RESPONSE:**

Excellent critique. Your trader is absolutely correct - you've identified the core conceptual errors in V26. Here are the corrected specifications for V27:

**V27 MTR LOGIC SPECS:**

```python
# -------------------------
# CANONICAL MTR DEFINITION
# -------------------------

# STAGE 0: TREND (Bear)
if TPI > threshold and clear_downtrend():
    swing_low = identify_swing_low()
    
    # STAGE 1: TREND LINE BREAK (Not just 1-bar high)
    # ------------------------------------------------
    # ANSWER TO Q1: Best proxy for diagonal trend line break:
    # "Close above 20EMA" OR "Rally of 0.5%+ from swing low"
    if close > ema20 or (high > swing_low * 1.005):  # First leg move
        stage1_confirmed = True
        break_high = current_high
        
        # STAGE 2: WEAKER MOMENTUM PULLBACK (Higher Low)
        # -----------------------------------------------
        # ANSWER TO Q2: Measure momentum weakening:
        # Compare Stage 0 decline vs Stage 2 decline metrics
        stage0_momentum = calculate_stage0_momentum()  # Fast, steep decline
        stage2_momentum = calculate_stage2_momentum()  # Shallow, hesitant
        
        # KEY METRIC: Stage 2 must show momentum decay
        if (is_higher_low() and 
            stage2_momentum["slope"] > stage0_momentum["slope"] and  # Less steep
            stage2_momentum["avg_body"] > stage0_momentum["avg_body"] and  # Smaller bear bodies
            macd_histogram_stage2 > macd_histogram_stage0):  # Less negative
            stage2_confirmed = True
            
            # STAGE 3: SIGNAL BAR (No EMA20 requirement)
            # ------------------------------------------
            # ANSWER TO Q3: Trigger is reversal pattern + context
            signal_bar = identify_reversal_bar()  # Bullish engulfing, hammer, etc.
            
            # KEY FILTERS (replaces EMA20 gate):
            if (signal_bar["bullish"] and
                signal_bar["close"] > signal_bar["open"] and
                signal_bar["close"] > (signal_bar["high"] + signal_bar["low"])/2 and  # Above midpoint
                volume_increasing() and
                is_not_lower_low()):  # Critical: prevents buying continuation
                
                # ENTRY LOGIC
                entry_price = signal_bar["close"]
                stop_loss = min(swing_low, stage2_low) - buffer  # Below recent low
                risk = entry_price - stop_loss
                
                # TARGET: 2R (not to old high)
                target_price = entry_price + (2 * risk)  # Exactly 2:1 R:R
                
                return {
                    "signal": "MTR_LONG",
                    "entry": entry_price,
                    "stop": stop_loss,
                    "target": target_price,
                    "r_multiple": 2.0
                }
```

**QUANTIFICATION ANSWERS:**

1. **Trend Line Break Proxy**: Use BOTH:
   - `Close > EMA20` (primary filter)
   - AND/OR `Rally > 0.5% from swing low` (catches stronger moves)
   This mimics "breaking the diagonal" - it's a substantial move, not just 1 bar.

2. **Momentum Weakening**: Triple confirmation:
   - **Slope Comparison**: Stage 2 decline slope > Stage 0 decline slope (less negative)
   - **Body Size**: Stage 2 average bear body < Stage 0 average bear body
   - **MACD Histogram**: Stage 2 histogram > Stage 0 histogram (less bearish)

3. **Early Signal Guardrails** (replacing EMA20):
   - Signal bar must close ABOVE its midpoint
   - Volume must be increasing on reversal
   - Must NOT be making a lower low (critical - filters continuation)
   - Bullish pattern (engulfing, hammer, inside bar breakout)

**CRITICAL CHANGES FROM V26:**
1. Stage 1: `close > ema20` replaces `h1_active` (1-bar high break)
2. Stage 2: Added momentum comparison (weakening filter)
3. Stage 3: Removed `close > ema20` requirement entirely
4. Target: Changed to 2R fixed target, not "distance to old high"
5. Added anti-continuation filter: `is_not_lower_low()`

**IMPLEMENTATION PRIORITY:**
1. Fix Stage 1 (trend line break) - most critical error
2. Add momentum comparison in Stage 2
3. Remove EMA20 gate from signal bar
4. Change target calculation to 2R fixed

This aligns with professional MTR trading: early entry on momentum shift confirmation, proper risk management with 2R target, and filtering for genuine reversals vs. continuations.