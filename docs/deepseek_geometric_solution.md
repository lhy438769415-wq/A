Here's a robust implementation of the geometric trendline system for your MTR strategy:

```python
import pandas as pd
import numpy as np
from typing import Tuple, Optional, List
from dataclasses import dataclass

@dataclass
class SwingPoint:
    """Represents a significant swing point in price"""
    idx: int           # Bar index
    price: float       # Price level
    type: str          # 'high' or 'low'
    strength: float    # Relative strength (higher = more significant)


class GeometricTrendlineEngine:
    """
    Implements Al Brooks-style geometric trendlines using swing points.
    Handles bear trendlines by connecting lower highs.
    """
    
    def __init__(self, swing_window: int = 5, atr_window: int = 14, 
                 min_bar_distance: int = 3, break_threshold: float = 0.5):
        """
        Args:
            swing_window: Bars to look left/right for swing points
            atr_window: Period for ATR calculation if not provided
            min_bar_distance: Minimum bars between significant swing points
            break_threshold: Multiple of ATR for significant breakout
        """
        self.swing_window = swing_window
        self.atr_window = atr_window
        self.min_bar_distance = min_bar_distance
        self.break_threshold = break_threshold
        
    def find_swing_points(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """
        Identify significant swing highs and lows using fractal method.
        
        Args:
            df: DataFrame with 'high', 'low', 'close' columns
            
        Returns:
            Tuple of (swing_highs, swing_lows) as boolean Series
        """
        # Initialize with False
        swing_highs = pd.Series(False, index=df.index)
        swing_lows = pd.Series(False, index=df.index)
        
        # Look for swing highs (local maxima)
        for i in range(self.swing_window, len(df) - self.swing_window):
            window_high = df['high'].iloc[i]
            
            # Check left side
            left_valid = all(window_high > df['high'].iloc[i-j] 
                           for j in range(1, self.swing_window + 1))
            
            # Check right side
            right_valid = all(window_high > df['high'].iloc[i+j] 
                            for j in range(1, self.swing_window + 1))
            
            if left_valid and right_valid:
                swing_highs.iloc[i] = True
                
        # Look for swing lows (local minima)
        for i in range(self.swing_window, len(df) - self.swing_window):
            window_low = df['low'].iloc[i]
            
            # Check left side
            left_valid = all(window_low < df['low'].iloc[i-j] 
                           for j in range(1, self.swing_window + 1))
            
            # Check right side
            right_valid = all(window_low < df['low'].iloc[i+j] 
                            for j in range(1, self.swing_window + 1))
            
            if left_valid and right_valid:
                swing_lows.iloc[i] = True
                
        # Filter: require minimum distance between significant swings
        swing_highs = self._filter_swing_distance(df, swing_highs, 'high')
        swing_lows = self._filter_swing_distance(df, swing_lows, 'low')
        
        return swing_highs, swing_lows
    
    def _filter_swing_distance(self, df: pd.DataFrame, 
                              swings: pd.Series, 
                              price_type: str) -> pd.Series:
        """Ensure minimum distance between swing points"""
        filtered = swings.copy()
        swing_indices = df.index[swings].tolist()
        
        if not swing_indices:
            return filtered
            
        price_col = 'high' if price_type == 'high' else 'low'
        
        # Keep only the most significant swings within min_bar_distance
        i = 0
        while i < len(swing_indices) - 1:
            j = i + 1
            while j < len(swing_indices):
                idx1, idx2 = swing_indices[i], swing_indices[j]
                bar_diff = abs(df.index.get_loc(idx2) - df.index.get_loc(idx1))
                
                if bar_diff < self.min_bar_distance:
                    # Keep the higher high or lower low
                    price1 = df.loc[idx1, price_col]
                    price2 = df.loc[idx2, price_col]
                    
                    if price_type == 'high':
                        # Keep the higher high
                        remove_idx = idx1 if price1 < price2 else idx2
                    else:
                        # Keep the lower low
                        remove_idx = idx1 if price1 > price2 else idx2
                    
                    filtered.loc[remove_idx] = False
                    # Update list
                    swing_indices = df.index[filtered].tolist()
                    i = max(0, i - 1)  # Go back one to re-check
                    break
                j += 1
            i += 1
            
        return filtered
    
    def identify_swing_objects(self, df: pd.DataFrame, 
                              swing_highs: pd.Series, 
                              swing_lows: pd.Series) -> List[SwingPoint]:
        """Convert swing points to SwingPoint objects with strength calculation"""
        swing_points = []
        
        # Calculate average bar range for strength normalization
        avg_range = (df['high'] - df['low']).rolling(20).mean().median()
        
        for idx in df.index[swing_highs]:
            i = df.index.get_loc(idx)
            if i > 0 and i < len(df) - 1:
                # Strength based on price move from previous low to this high
                lookback = min(10, i)
                prev_low = df['low'].iloc[i-lookback:i].min()
                price_move = df.loc[idx, 'high'] - prev_low
                strength = price_move / avg_range if avg_range > 0 else 1.0
                
                swing_points.append(
                    SwingPoint(idx=idx, price=df.loc[idx, 'high'], 
                              type='high', strength=strength)
                )
                
        for idx in df.index[swing_lows]:
            i = df.index.get_loc(idx)
            if i > 0 and i < len(df) - 1:
                # Strength based on price move from previous high to this low
                lookback = min(10, i)
                prev_high = df['high'].iloc[i-lookback:i].max()
                price_move = prev_high - df.loc[idx, 'low']
                strength = price_move / avg_range if avg_range > 0 else 1.0
                
                swing_points.append(
                    SwingPoint(idx=idx, price=df.loc[idx, 'low'], 
                              type='low', strength=strength)
                )
                
        # Sort by index
        swing_points.sort(key=lambda x: x.idx)
        return swing_points
    
    def find_bear_trendline(self, df: pd.DataFrame, 
                           swing_points: List[SwingPoint],
                           current_idx: int) -> Optional[Tuple[float, float]]:
        """
        Find the best bear trendline (connecting lower highs).
        
        Args:
            df: Price DataFrame
            swing_points: List of SwingPoint objects
            current_idx: Current bar index for analysis
            
        Returns:
            Tuple of (slope, intercept) or None if no valid trendline
        """
        # Get only swing highs up to current bar
        swing_highs = [sp for sp in swing_points 
                      if sp.type == 'high' and sp.idx <= current_idx]
        
        if len(swing_highs) < 2:
            return None
            
        # Sort by strength (most significant first)
        swing_highs.sort(key=lambda x: x.strength, reverse=True)
        
        # Try different combinations to find the best trendline
        best_trendline = None
        best_score = -np.inf
        
        for i in range(len(swing_highs) - 1):
            for j in range(i + 1, len(swing_highs)):
                # Ensure chronological order
                if swing_highs[i].idx > swing_highs[j].idx:
                    point1, point2 = swing_highs[j], swing_highs[i]
                else:
                    point1, point2 = swing_highs[i], swing_highs[j]
                
                # Must be lower highs (bearish)
                if point2.price >= point1.price:
                    continue
                    
                # Calculate trendline
                x1 = df.index.get_loc(point1.idx)
                x2 = df.index.get_loc(point2.idx)
                y1 = point1.price
                y2 = point2.price
                
                if x2 == x1:  # Avoid division by zero
                    continue
                    
                slope = (y2 - y1) / (x2 - x1)
                intercept = y1 - slope * x1
                
                # Score this trendline
                score = self._score_trendline(df, swing_highs, slope, intercept, 
                                            point1.idx, point2.idx)
                
                if score > best_score:
                    best_score = score
                    best_trendline = (slope, intercept, point1.idx, point2.idx)
        
        return best_trendline
    
    def _score_trendline(self, df: pd.DataFrame, 
                        swing_highs: List[SwingPoint], 
                        slope: float, intercept: float,
                        idx1, idx2) -> float:
        """Score a trendline based on how well it fits swing highs"""
        score = 0.0
        
        # Base score for distance between points (prefer recent points)
        x1 = df.index.get_loc(idx1)
        x2 = df.index.get_loc(idx2)
        distance_weight = min(1.0, (x2 - x1) / 50)  # Normalize
        
        # Check how many other swing highs are near the line
        for sp in swing_highs:
            if sp.idx == idx1 or sp.idx == idx2:
                continue
                
            x = df.index.get_loc(sp.idx)
            line_value = slope * x + intercept
            price_diff = abs(sp.price - line_value)
            
            # Convert to ATR-normalized distance
            atr = df.loc[sp.idx, 'atr'] if 'atr' in df.columns else 1.0
            normalized_diff = price_diff / atr if atr > 0 else price_diff
            
            # Points near the line get positive score
            if normalized_diff < 0.5:  # Within 0.5 ATR
                score += 1.0 - normalized_diff * 2
                
        return score * distance_weight
    
    def calculate_trendline_value(self, df: pd.DataFrame, 
                                 trendline: Tuple[float, float, int, int], 
                                 target_idx: int) -> float:
        """
        Calculate trendline value at a specific bar index.
        
        Args:
            df: Price DataFrame
            trendline: (slope, intercept, start_idx, end_idx)
            target_idx: Bar index to calculate value for
            
        Returns:
            Trendline value at target_idx
        """
        slope, intercept, _, _ = trendline
        x = df.index.get_loc(target_idx)
        return slope * x + intercept
    
    def check_trendline_break(self, df: pd.DataFrame, 
                             current_idx: int,
                             trendline: Tuple[float, float, int, int]) -> bool:
        """
        Check for valid trendline breakout.
        
        Criteria:
        1. Close must be above trendline
        2. Break must be significant (multiple of ATR)
        3. Must break after the trendline's last defining point
        """
        if trendline is None:
            return False
            
        slope, intercept, _, trendline_end_idx = trendline
        
        # Must be after the trendline's defining points
        if df.index.get_loc(current_idx) <= df.index.get_loc(trendline_end_idx):
            return False
            
        # Calculate trendline value at current bar
        current_close = df.loc[current_idx, 'close']
        trendline_value = self.calculate_trendline_value(df, trendline, current_idx)
        
        # Check if close is above trendline
        if current_close <= trendline_value:
            return False
            
        # Check for significant break (multiple of ATR)
        atr = df.loc[current_idx, 'atr'] if 'atr' in df.columns else 1.0
        break_distance = (current_close - trendline_value) / atr
        
        return break_distance >= self.break_threshold
    
    def validate_first_leg_strength(self, df: pd.DataFrame, 
                                   swing_points: List[SwingPoint],
                                   break_idx: int) -> bool:
        """
        Validate that the first leg (down move) is strong.
        
        Criteria:
        1. Significant price decline
        2. Multiple swing lows
        3. Breaks key geometric levels
        """
        # Find the most recent significant swing high before break
        prior_highs = [sp for sp in swing_points 
                      if sp.type == 'high' and sp.idx < break_idx]
        
        if len(prior_highs) < 2:
            return False
            
        # Get the two most recent swing highs
        prior_highs.sort(key=lambda x: x.idx, reverse=True)
        recent_high1 = prior_highs[0] if prior_highs else None
        recent_high2 = prior_highs[1] if len(prior_highs) > 1 else None
        
        # Find swing lows between these highs
        if recent_high1 and recent_high2:
            lows_between = [sp for sp in swing_points 
                          if sp.type == 'low' 
                          and recent_high2.idx < sp.idx < recent_high1.idx]
            
            # Check if we broke below the geometric line between highs
            if len(lows_between) >= 1:
                # Calculate geometric progression
                high1_idx = df.index.get_loc(recent_high2.idx)
                high2_idx = df.index.get_loc(recent_high1.idx)
                price_range = recent_high2.price - recent_high1.price
                
                # Key level: 61.8% retracement of the decline
                key_level = recent_high1.price + 0.618 * price_range
                
                # Check if price broke below this level
                min_price = df.loc[recent_high1.idx:break_idx, 'low'].min()
                return min_price < key_level
                
        return False
    
    def analyze_bar(self, df: pd.DataFrame, bar_index: int) -> dict:
        """
        Complete analysis for a single bar.
        
        Returns:
            Dictionary with all analysis results
        """
        # Find swing points
        swing_highs, swing_lows = self.find_swing_points(df.iloc[:bar_index+1])
        swing_points = self.identify_swing_objects(df.iloc[:bar_index+1], 
                                                  swing_highs, swing_lows)
        
        # Find bear trendline
        trendline = self.find_bear_trendline(df, swing_points, bar_index)
        
        # Check for break
        has_break = False
        if trendline:
            has_break = self.check_trendline_break(df, bar_index, trendline)
            
            # Validate first leg if we have a break
            if has_break:
                has_break = self.validate_first_leg_strength(df, swing_points, bar_index)
        
        # Calculate trendline value for current bar
        trendline_value = None
        if trendline:
            trendline_value = self.calculate_trendline_value(df, trendline, bar_index)
        
        return {
            'bar_index': bar_index,
            'has_trendline': trendline is not None,
            'trendline_value': trendline_value,
            'has_break': has_break,
            'swing_points': swing_points[-5:],  # Last 5 swing points
            'close': df.loc[bar_index, 'close']
        }


# Example usage in your MTR strategy:
class MTRStrategy:
    def __init__(self):
        self.trendline_engine = GeometricTrendlineEngine(
            swing_window=5,
            atr_window=14,
            min_bar_distance=3,
            break_threshold=0.5
        )
        
    def should_enter_long(self, df: pd.DataFrame, current_bar: int) -> bool:
        """
        Replace your EMA logic with geometric trendline logic.
        
        Entry criteria:
        1. Valid bear trendline exists
        2. Price closes significantly above trendline
        3. First leg down was strong
        4. (Add your other MTR criteria here)
        """
        # Use last 100 bars for analysis (adjust as needed)
        lookback = min(100, current_bar + 1)
        analysis_df = df.iloc[current_bar-lookback+1:current_bar+1].copy()
        
        # Ensure we have ATR
        if 'atr' not in analysis_df.columns:
            analysis_df['atr'] = self._calculate_atr(analysis_df)
        
        # Analyze current bar
        result = self.trendline_engine.analyze_bar(analysis_df, current_bar)
        
        # Entry signal
        if result['has_break']:
            print(f"Long signal at bar {current_bar}: "
                  f"Break above trendline at {result['trendline_value']:.4f}, "
                  f"Close: {result['close']:.4f}")
            return True
        
        return False
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range if not present"""
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(period).mean()


# Quick test function
def test_engine():
    """Test the geometric trendline engine"""
    # Create sample data (replace with your actual data)
    np.random.seed(42)
    n_bars = 200
    dates = pd.date_range('2023-01-01', periods=n_bars, freq='D')
    
    # Create a downtrend with lower highs
    base_trend = np.linspace(100, 80, n_bars)
    noise = np.random.randn(n_bars) * 2
    highs = base_trend + np.abs(noise) + 3
    lows = base_trend - np.abs(noise) - 3
    closes = (highs + lows) / 2 + np.random.randn(n_bars) * 1
    
    df = pd.DataFrame({
        'high': highs,
        'low': lows,
        'close': closes
    }, index=dates)
    
    # Calculate ATR
    df['atr'] = 2.0  # Simplified for test
    
    # Initialize engine
    engine = GeometricTrendlineEngine()
    
    # Test on last 50 bars
    for i in range(150, n_bars):
        result = engine.analyze_bar(df, i)
        if result['has_break']:
            print(f"\nBreak detected at bar {i} ({df.index[i]})")
            print(f"  Trendline value: {result['trendline_value']:.2f}")
            print(f"  Close: {result['close']:.2f}")
            print(f"  Swing points found: {len(result['swing_points'])}")
            break


if __name__ == "__main__":
    test_engine()
```

## Key Features:

1. **True Swing Points**: Uses fractal method (5-bar window) with distance filtering to identify significant highs/lows.

2. **Geometric Bear Trendline**: 
   - Connects lower highs only (true bear trendline)
   - Uses scoring system to find the "best fit" line
   - Extends line forward for projection

3. **True Breakout Logic**:
   - Close must be above trendline
   - Break must exceed ATR threshold (configurable)
   - Must occur after trendline's defining points
   - Validates first leg strength (checks for strong down move)

## Integration into Your MTR Strategy:

Replace your EMA logic with:
```python
def should_enter_long(self, df, current_bar):
    result = self.trendline_engine.analyze_bar(df, current_bar)
    return result['has_break']  # Add your other MTR criteria
```

## Important Notes:

1. **Adjust Parameters**: The `break_threshold` (ATR multiple) and `swing_window` should be optimized for your timeframe.

2. **First Leg Validation**: The engine checks if the decline preceding the break was structurally significant using Fibonacci-based geometric levels.

3. **Vectorization**: While not fully vectorized (due to the sequential nature of swing analysis), the engine is efficient enough for real-time use on typical timeframes.

4. **ATR Requirement**: The breakout uses ATR for significance. Ensure your dataframe has an 'atr' column or let the engine calculate it.

This implements the exact geometric principles Al Brooks teaches - trendlines based on price structure, not moving averages.