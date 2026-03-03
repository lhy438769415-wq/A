import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

WATCHLIST_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'watchlist.json')

class WatchlistManager:
    def __init__(self, path=WATCHLIST_PATH):
        self.path = path
        self.data = {}
        self.load()
        
    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load watchlist: {e}")
                self.data = {}
        else:
            self.data = {}
            
    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save watchlist: {e}")

    def add_signal(self, code, entry, sl, score, sb_idx, date):
        self.data[code] = {
            "status": "NEW", 
            "entry": float(entry) if entry else 0.0,
            "sl": float(sl) if sl else 0.0,
            "score": float(score) if score else 0.0,
            "signal_date": date,
            "signal_bar_idx": int(sb_idx),
            "added_date": datetime.now().strftime('%Y-%m-%d'),
            "days_watching": 0
        }
        self.save()
        
    def update_status(self, code, df):
        if code not in self.data:
            return None
            
        signal = self.data[code]
        if signal['status'] in ['TRIGGERED', 'INVALIDATED', 'EXPIRED']:
            return signal['status']
            
        if len(df) == 0:
            return signal['status']
            
        latest = df.iloc[-1]
        today_date = latest.name.strftime('%Y-%m-%d') if hasattr(latest.name, 'strftime') else str(latest.name)
        if 'date' in df.columns:
            today_date = str(latest['date'])
            
        # [Bug Fix] 每次更新时自动计算追踪天数（基于 added_date 与今日之差）
        try:
            added = datetime.strptime(signal.get('added_date', today_date), '%Y-%m-%d')
            signal['days_watching'] = (datetime.now() - added).days
        except (ValueError, TypeError):
            pass

        # condition checks
        if latest['high'] >= signal['entry'] and signal['entry'] > 0:
            signal['status'] = 'TRIGGERED'
            self.save()
            return signal['status']
            
        if latest['low'] <= signal['sl'] and signal['sl'] > 0:
            signal['status'] = 'INVALIDATED'
            self.save()
            return signal['status']
            
        # NEW to WATCHING transition
        # When new day comes, change status to watching
        if signal['status'] == 'NEW' and today_date != signal['signal_date']:
            signal['status'] = 'WATCHING'

        self.save()
        return signal['status']

    def update_signal_bar(self, code, new_sb_idx, new_entry):
        if code in self.data:
            self.data[code]['signal_bar_idx'] = int(new_sb_idx)
            self.data[code]['entry'] = float(new_entry)
            self.data[code]['status'] = 'UPDATED' # Just a marker, will act like watching
            self.save()

    def get_by_status(self, status):
        return {k: v for k, v in self.data.items() if v['status'] == status}
        
    def get_new(self): return self.get_by_status("NEW")
    def get_watching(self): 
        # include updated as watching
        watching = self.get_by_status("WATCHING")
        updated = self.get_by_status("UPDATED")
        watching.update(updated)
        return watching
    def get_expired(self): return self.get_by_status("EXPIRED")
    def get_all(self): return self.data
