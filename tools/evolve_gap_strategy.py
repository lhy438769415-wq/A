# -*- coding: utf-8 -*-
"""
Evolution Engine for Structural Gap Strategy
"""
import os
import sys
import json
import logging
import pandas as pd
import numpy as np

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.api_client import query_deepseek

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

RULES_FILE = os.path.join(project_root, 'config', 'gap_optimized_rules.json')
DATA_FILE = os.path.join(project_root, 'data', 'struct_gap_features.csv')

def load_or_extract_features():
    if not os.path.exists(DATA_FILE):
        logger.info("Features file not found. Running extraction... This may take a while.")
        import tools.extract_struct_gap_features as extractor
        extractor.main()
    
    if os.path.exists(DATA_FILE):
        return pd.read_csv(DATA_FILE)
    return None

def analyze_and_prompt(df: pd.DataFrame) -> str:
    total = len(df)
    win_rate = df['result'].mean() * 100
    
    feature_stats = ""
    features = ['gap_size_atr', 'sig_bar_quality', 'retracement_depth']
    for col in features:
        if col not in df.columns:
            continue
        try:
            # Dropna for the specified column to avoid binning issues
            valid_df = df.dropna(subset=[col])
            bins = pd.qcut(valid_df[col], q=5, duplicates='drop')
            grouped = valid_df.groupby(bins, observed=True)['result'].agg(['count', 'mean'])
            grouped['mean'] = (grouped['mean'] * 100).round(2)
            feature_stats += f"\nFeature [{col}] Distribution Performance:\n{grouped.to_string()}\n"
        except Exception as e:
            logger.debug(f"Could not bin {col}: {e}")
            
    prompt = f"""
# ROLE: OpenClaw - PA Strategy Optimization Agent
You are evaluating the Structural Gap Strategy based on historical performance data. Your goal is to extract hard thresholds for the strategy's filtering logic to maximize the mathematical expectation (E) while maintaining the logic of Al Brooks' Price Action.

## Baseline Performance
Total Samples (Trades): {total}
Win Rate: {win_rate:.2f}%

## Feature Distribution Analysis
{feature_stats}

## Optimization Task
Based on the data above, identify the most critical thresholds that can eliminate the most toxic trades (low Win Rate bins) while keeping the high-quality trades. Be very conservative: do not eliminate more than 30% of the total successful trades (WINs).

Provide your response strictly as a JSON object containing the optimized thresholds. If a threshold is not necessary, omit it from the JSON. The JSON keys you may use are:
- `min_sig_quality`
- `min_gap_size_atr`
- `max_retracement_depth`

The JSON must match this structure exactly:
```json
{{
    "min_sig_quality": 0.5,
    "min_gap_size_atr": 0.0,
    "max_retracement_depth": 1.0,
    "reasoning": "Brief explanation of why these thresholds were chosen based on the data."
}}
```
Do not output any markdown formatting or text outside the JSON block.
"""
    return prompt

def test_new_rules(df, rules) -> dict:
    cond = pd.Series(True, index=df.index)
    if 'min_sig_quality' in rules:
        cond &= (df['sig_bar_quality'] >= rules['min_sig_quality'])
    if 'min_gap_size_atr' in rules:
        cond &= (df['gap_size_atr'] >= rules['min_gap_size_atr'])
    if 'max_retracement_depth' in rules:
        cond &= (df['retracement_depth'] <= rules['max_retracement_depth'])
        
    filtered_df = df[cond]
    if len(filtered_df) == 0:
        return {'total': 0, 'win_rate': 0.0}
        
    new_wr = filtered_df['result'].mean() * 100
    return {
        'total': len(filtered_df),
        'win_rate': new_wr
    }

def main():
    logger.info("🚀 Starting Gap Strategy Evolution Loop...")
    df = load_or_extract_features()
    if df is None or len(df) == 0:
        logger.error("No feature data available to evolve.")
        return
        
    # Exclude TIMEOUT or non-closed trades
    if 'status' in df.columns:
        df = df[df['status'].isin(['WIN', 'LOSS'])]
    
    prompt = analyze_and_prompt(df)
    logger.info("🧠 Sending data to AI for optimization analysis...")
    
    response = query_deepseek(prompt)
    
    import re
    json_match = re.search(r'\{.*\}', response, re.DOTALL)
    if json_match:
        try:
            rules_json = json.loads(json_match.group(0))
            logger.info(f"✨ AI proposed new rules: {rules_json}")
            
            baseline_wr = df['result'].mean() * 100
            new_stats = test_new_rules(df, rules_json)
            logger.info(f"📊 Baseline: {len(df)} samples, Win Rate {baseline_wr:.2f}%")
            logger.info(f"📊 OOS / New Rules: {new_stats['total']} samples, Win Rate {new_stats['win_rate']:.2f}%")
            
            if new_stats['win_rate'] > baseline_wr and new_stats['total'] >= 10:
                os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
                # Remove reasoning from the saved rules to keep config clean
                save_rules = {k: v for k, v in rules_json.items() if k != 'reasoning'}
                with open(RULES_FILE, 'w', encoding='utf-8') as f:
                    json.dump(save_rules, f, indent=4, ensure_ascii=False)
                logger.info(f"✅ Evolution successful! Rules saved to {RULES_FILE}")
            else:
                logger.warning("📉 New rules did not significantly improve performance or left too few samples. Discarding.")
                
        except Exception as e:
            logger.error(f"Failed to parse AI response: {e}\nResponse: {response}")
    else:
        logger.error(f"No JSON found in AI response.\nResponse: {response}")

if __name__ == '__main__':
    main()
