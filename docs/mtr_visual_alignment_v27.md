# MTR V27 Visual Alignment

## Context

    We are aligning our Python Strategy to a specific "Standard MTR Chart" provided by the user.
    
    VISUAL PATTERN DESCRIPTION:
    1. **Strong Bear Trend**: Price drops significantly (Red Dot Low). Trend Line is steep.
    2. **Trend Line Break (First Leg)**: A distinct rally (Blue Arrow) that breaks the diagonal descent. 
       - *Challenge*: We can't draw diagonal lines in code.
       - *Requirement*: We need a robust "Proxy" that signifies this physical break.
    3. **The Pullback (Test)**: A drift down (Grey Arrow) that forms a Higher Low (Green Dot).
       - It is visibly "weaker" than the original drop.
    4. **The Entry (Signal)**: A reversal bar (Orange Arrow) triggering the trade.

    OBJECTIVE:
    Translate this Visual Morphology into Vectorized Logic.

    QUESTIONS FOR AL BROOKS AI:
    1. **The "Blue Line" Proxy**: 
       - How do we define the "Trend Line Break" without drawing a line? 
       - Is "Close > Prior Major Lower High" the standard? 
       - Or is it "N Bars wholly above EMA"? (The Gap).
       - Please provide the *Best Practice* proxy.

    2. **The "Grey Pullback" Physics**:
       - How do we code "Weaker Momentum"? 
       - Is it "Slope < Prior Slope"?
       - Is it "Percentage Retracement"? (e.g. 50-70%)?
       - How do we filter out pullbacks that are just "Bear Flags" continuing down?

    3. **The "Green Dot" Timing**:
       - The user notes "Waiting for Close > EMA20 is too late".
       - What is the *earliest valid trigger* at the Higher Low? 
       - Is it a "High 2" entry? A "Key Reversal Bar"?

    Please act as the architect and provide the logic steps for V27.
    

## Response
## **V27 ARCHITECTURE: Standard MTR Pattern Logic**

### **1. TREND LINE BREAK PROXY (Blue Arrow)**
**Best Practice Definition:**
```python
# Core Logic
trend_break_condition = (
    (close > prior_major_lower_high) &
    (close > rolling_max(low, window=20)) &  # Break above recent consolidation
    (rsi > 40) &  # Momentum confirmation
    (volume > sma(volume, 10))  # Volume confirmation
)
```

**Why this works:**
- `prior_major_lower_high` = highest high of the last 5-10 bars before current downtrend
- The `rolling_max` condition captures the "physical barrier" of the trend line
- RSI > 40 ensures momentum shift (not oversold bounce)
- Volume confirms institutional participation

**Alternative (if no clear swing high):**
```python
ema_gap_break = (
    (close > ema20) & 
    (high - ema20) / ema20 > 0.01  # 1% gap above EMA
)
```

### **2. WEAK PULLBACK PHYSICS (Grey Arrow)**
**Vectorized Implementation:**
```python
# Momentum Comparison
initial_drop_slope = (red_dot_low - start_of_drop_high) / bars_in_drop
pullback_slope = (pullback_low - blue_arrow_high) / bars_in_pullback

weaker_momentum = (
    (abs(pullback_slope) < abs(initial_drop_slope) * 0.6) &  # 40% less steep
    (retracement_percent >= 50) & (retracement_percent <= 70) &
    (atr_pullback < atr_initial_drop)  # Volatility contraction
)

# Filter Out Bear Flags
not_bear_flag = (
    (rsi_divergence == True) |  # Bullish divergence
    (pullback_volume < rally_volume * 0.8)  # Lower volume on pullback
)
```

**Key Metrics:**
- `retracement_percent = (pullback_low - blue_arrow_high) / (blue_arrow_high - red_dot_low) * 100`
- Use ATR ratio for volatility comparison
- Require at least 3-5 bars for pullback (not 1-bar spike)

### **3. EARLY ENTRY TRIGGER (Green Dot → Orange Arrow)**
**Earliest Valid Signal (Not waiting for EMA close):**
```python
entry_trigger = (
    # Pattern Conditions
    (higher_low_confirmed) &
    (
        # OPTION 1: Key Reversal Bar
        ((low < previous_low) & (close > previous_high)) |
        
        # OPTION 2: High 2 Setup (MTR Specific)
        (
            (current_low > pullback_low) &  # Test of pullback low
            (close > open) &  # Bullish bar
            (close > (high + low) / 2) &  # Strong close
            (volume > sma(volume, 5))  # Volume spike
        ) |
        
        # OPTION 3: Inside Bar Break
        (
            (high < previous_high) & (low > previous_low) &  # Inside bar
            (close[current] > high[previous])  # Breakout
        )
    )
)

higher_low_confirmed = (
    (low > red_dot_low) &  # Actual higher low
    (close > ema10) &  # But only 10 EMA for early signal
    (rsi > 45)  # Momentum confirming higher low
)
```

### **4. COMPLETE V27 LOGIC FLOW**

```python
def detect_mtr_pattern(data, params):
    """
    Vectorized MTR Pattern Detection
    """
    # 1. Identify Strong Bear Trend
    bear_trend = (
        (data['close'] < data['ema20']) &
        (data['ema20'] < data['ema50']) &
        (data['slope_20'] < -0.1)  # Steep decline
    )
    
    # 2. Find Red Dot Low (significant low in bear trend)
    red_dot_low = data['low'].rolling(window=10).min()
    red_dot_idx = data['low'] == red_dot_low
    
    # 3. Trend Line Break Proxy
    prior_major_high = data['high'].rolling(window=10).max().shift(10)
    trend_break = (
        (data['close'] > prior_major_high) &
        (data['close'] > data['low'].rolling(window=20).max()) &
        (data['rsi'] > 40) &
        bear_trend.shift(5)  # Came from bear trend
    )
    
    # 4. Blue Arrow Rally
    blue_arrow_high = data['high'].where(trend_break).ffill()
    
    # 5. Weak Pullback Detection
    pullback_low = data['low'].rolling(window=5).min()
    retracement = (blue_arrow_high - pullback_low) / (blue_arrow_high - red_dot_low)
    
    weak_pullback = (
        (retracement.between(0.5, 0.7)) &
        (data['atr'] < data['atr'].rolling(window=20).mean()) &
        (data['rsi'] > data['rsi'].shift(5))  # Bullish divergence
    )
    
    # 6. Higher Low Confirmation
    higher_low = (
        (pullback_low > red_dot_low) &
        (data['close'] > data['ema10']) &
        (data['rsi'] > 45)
    )
    
    # 7. Entry Trigger
    entry_signal = (
        higher_low &
        (
            # Key reversal
            ((data['low'] < data['low'].shift(1)) & 
             (data['close'] > data['high'].shift(1))) |
            # Strong bullish bar
            ((data['close'] - data['open']) / data['atr'] > 1.5) &
            (data['volume'] > data['volume'].rolling(20).mean() * 1.2)
        )
    )
    
    # 8. Full Pattern
    mtr_pattern = (
        bear_trend.shift(20) &  # Was in bear trend
        red_dot_idx.shift(15) &  # Had red dot low
        trend_break.shift(10) &  # Then broke trend
        weak_pullback.shift(5) &  # Weak pullback
        higher_low.shift(3) &  # Higher low formed
        entry_signal  # Entry trigger
    )
    
    return mtr_pattern
```

### **CRITICAL PARAMETERS FOR TUNING:**
```python
MTR_PARAMS = {
    'trend_slope_threshold': -0.1,  # Minimum steepness
    'retracement_min': 0.5,  # 50% Fibonacci
    'retracement_max': 0.7,  # 70% Fibonacci
    'volume_spike_ratio': 1.2,  # 20% above average
    'atr_contraction_ratio': 0.8,  # 20% less volatility
    'rsi_divergence_window': 5,  # Bars for divergence check
    'min_bars_between_phases': 3,  # Avoid compression
}
```

### **VALIDATION RULES:**
1. **Sequence Validation**: Ensure phases occur in correct order (Bear → Red Dot → Break → Pullback → Entry)
2. **Time Compression Filter**: Minimum 10 bars between Red Dot and Entry
3. **False Break Filter**: Require 2+ consecutive closes above trend line
4. **Volume Profile**: Rally volume > Pullback volume > Bear volume

This architecture captures the visual morphology while maintaining computational efficiency through vectorized operations.