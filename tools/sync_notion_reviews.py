# -*- coding: utf-8 -*-
"""
Notion 复盘日志同步工具 (tools/sync_notion_reviews.py)

通过 Notion API 拉取 PA日志 Database 的完整内容:
  1. 所有结构化属性列 (表格字段)
  2. 每条记录的页面富文本内容 (长文总结)
  3. 页面中的截图附件 (下载到本地)

使用方法:
  1. 在 .env 中配置 NOTION_TOKEN 和 NOTION_DB_ID
  2. python tools/sync_notion_reviews.py          # 全量同步
  3. python tools/sync_notion_reviews.py --discover # 仅探测数据库结构
"""

import os
import sys
import json
import logging
import requests
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =====================================================================
# 配置
# =====================================================================
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DB_ID = os.getenv("NOTION_DB_ID", "")

# 截图/附件保存路径
ATTACHMENTS_DIR = os.path.join(project_root, "data", "review_attachments")

# =====================================================================
# Notion 属性值提取器 (支持所有 Notion 属性类型)
# =====================================================================
def extract_property_value(prop: dict) -> str:
    """
    将 Notion API 返回的 property 对象提取为 Python 可用的字符串/数值。
    支持所有常见属性类型。
    """
    prop_type = prop.get('type', '')

    if prop_type == 'title':
        return _extract_rich_text(prop.get('title', []))

    elif prop_type == 'rich_text':
        return _extract_rich_text(prop.get('rich_text', []))

    elif prop_type == 'number':
        val = prop.get('number')
        return val if val is not None else ''

    elif prop_type == 'select':
        sel = prop.get('select')
        return sel.get('name', '') if sel else ''

    elif prop_type == 'multi_select':
        items = prop.get('multi_select', [])
        return ', '.join(item.get('name', '') for item in items)

    elif prop_type == 'status':
        status = prop.get('status')
        return status.get('name', '') if status else ''

    elif prop_type == 'date':
        date_obj = prop.get('date')
        if not date_obj:
            return ''
        start = date_obj.get('start', '')
        end = date_obj.get('end', '')
        return f"{start} ~ {end}" if end else start

    elif prop_type == 'checkbox':
        return prop.get('checkbox', False)

    elif prop_type == 'url':
        return prop.get('url', '') or ''

    elif prop_type == 'email':
        return prop.get('email', '') or ''

    elif prop_type == 'phone_number':
        return prop.get('phone_number', '') or ''

    elif prop_type == 'formula':
        formula = prop.get('formula', {})
        f_type = formula.get('type', '')
        return formula.get(f_type, '')

    elif prop_type == 'rollup':
        rollup = prop.get('rollup', {})
        r_type = rollup.get('type', '')
        if r_type == 'number':
            return rollup.get('number', '')
        elif r_type == 'array':
            arr = rollup.get('array', [])
            return ', '.join(str(extract_property_value(item)) for item in arr)
        return str(rollup.get(r_type, ''))

    elif prop_type == 'relation':
        relations = prop.get('relation', [])
        return ', '.join(r.get('id', '') for r in relations)

    elif prop_type == 'people':
        people = prop.get('people', [])
        return ', '.join(p.get('name', p.get('id', '')) for p in people)

    elif prop_type == 'files':
        files = prop.get('files', [])
        urls = []
        for f in files:
            if f.get('type') == 'file':
                urls.append(f['file'].get('url', ''))
            elif f.get('type') == 'external':
                urls.append(f['external'].get('url', ''))
        return urls  # 返回列表, 下载逻辑另处理

    elif prop_type == 'created_time':
        return prop.get('created_time', '')

    elif prop_type == 'last_edited_time':
        return prop.get('last_edited_time', '')

    elif prop_type == 'created_by':
        user = prop.get('created_by', {})
        return user.get('name', user.get('id', ''))

    elif prop_type == 'last_edited_by':
        user = prop.get('last_edited_by', {})
        return user.get('name', user.get('id', ''))

    elif prop_type == 'unique_id':
        uid = prop.get('unique_id', {})
        prefix = uid.get('prefix', '')
        number = uid.get('number', '')
        return f"{prefix}-{number}" if prefix else str(number)

    else:
        return str(prop)


def _extract_rich_text(text_array: list) -> str:
    """从 Notion rich_text 数组中提取纯文本"""
    return ''.join(item.get('plain_text', '') for item in text_array)


# =====================================================================
# Notion 页面内容提取器 (Block Children → 纯文本 + 图片URL)
# =====================================================================
def extract_page_content(page_id: str, headers: dict) -> dict:
    """
    提取一个 Notion 页面的所有 Block 内容。

    Returns:
        dict: {
            'full_text': str,     # 所有文本块合并
            'image_urls': list,   # 所有图片 URL
        }
    """
    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    full_text_parts = []
    image_urls = []

    while url:
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"获取页面内容失败 {page_id}: {e}")
            break

        for block in data.get('results', []):
            block_type = block.get('type', '')

            # 文本类 Block
            if block_type in ('paragraph', 'heading_1', 'heading_2', 'heading_3',
                               'bulleted_list_item', 'numbered_list_item',
                               'toggle', 'quote', 'callout', 'to_do'):
                text_arr = block.get(block_type, {}).get('rich_text', [])
                text = _extract_rich_text(text_arr)
                if text:
                    if block_type.startswith('heading'):
                        full_text_parts.append(f"\n## {text}\n")
                    elif block_type == 'bulleted_list_item':
                        full_text_parts.append(f"- {text}")
                    elif block_type == 'numbered_list_item':
                        full_text_parts.append(f"1. {text}")
                    elif block_type == 'quote':
                        full_text_parts.append(f"> {text}")
                    else:
                        full_text_parts.append(text)

            # 图片 Block
            elif block_type == 'image':
                img = block.get('image', {})
                img_type = img.get('type', '')
                if img_type == 'file':
                    img_url = img['file'].get('url', '')
                elif img_type == 'external':
                    img_url = img['external'].get('url', '')
                else:
                    img_url = ''
                if img_url:
                    image_urls.append(img_url)
                    # 图片的 caption
                    caption = _extract_rich_text(img.get('caption', []))
                    full_text_parts.append(f"[图片: {caption}]" if caption else "[图片]")

            # 代码块
            elif block_type == 'code':
                code_text = _extract_rich_text(block.get('code', {}).get('rich_text', []))
                if code_text:
                    full_text_parts.append(f"```\n{code_text}\n```")

            # 分割线
            elif block_type == 'divider':
                full_text_parts.append("---")

        # 分页处理
        if data.get('has_more'):
            cursor = data.get('next_cursor')
            url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100&start_cursor={cursor}"
        else:
            url = None

    return {
        'full_text': '\n'.join(full_text_parts),
        'image_urls': image_urls,
    }


# =====================================================================
# 图片下载器
# =====================================================================
def download_image(url: str, save_dir: str, filename: str) -> str:
    """下载图片并返回本地路径"""
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, filename)

    if os.path.exists(save_path):
        return save_path

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        with open(save_path, 'wb') as f:
            f.write(resp.content)
        return save_path
    except Exception as e:
        logger.warning(f"图片下载失败: {e}")
        return ''


# =====================================================================
# 核心: 数据库结构探测
# =====================================================================
def discover_database(notion_token: str, db_id: str) -> dict:
    """
    探测 Notion 数据库的完整结构。

    Returns:
        dict: {'properties': {name: type}, 'sample_row': dict}
    """
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    # 获取数据库 schema
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        db_info = resp.json()
    except Exception as e:
        logger.error(f"获取数据库结构失败: {e}")
        return {}

    properties = {}
    for name, prop in db_info.get('properties', {}).items():
        properties[name] = prop.get('type', 'unknown')

    # 拉一条样本数据
    query_url = f"https://api.notion.com/v1/databases/{db_id}/query"
    try:
        resp = requests.post(query_url, headers=headers,
                             json={"page_size": 1}, timeout=30)
        resp.raise_for_status()
        results = resp.json().get('results', [])
        sample = {}
        if results:
            page = results[0]
            for name, prop in page.get('properties', {}).items():
                sample[name] = extract_property_value(prop)
    except Exception as e:
        logger.warning(f"获取样本数据失败: {e}")
        sample = {}

    return {
        'title': _extract_rich_text(db_info.get('title', [])),
        'properties': properties,
        'sample_row': sample,
        'total_props': len(properties),
    }


# =====================================================================
# 核心: 全量同步
# =====================================================================
def sync_all(notion_token: str, db_id: str,
             fetch_content: bool = True,
             download_images: bool = True) -> list:
    """
    全量同步 Notion PA日志数据库。

    1. 遍历所有数据库条目 (分页)
    2. 提取每条的所有属性值
    3. (可选) 提取每个页面的富文本内容
    4. (可选) 下载页面中的截图

    Returns:
        list[dict]: 所有条目的结构化数据
    """
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    query_url = f"https://api.notion.com/v1/databases/{db_id}/query"
    all_records = []
    has_more = True
    start_cursor = None
    page_num = 0

    while has_more:
        page_num += 1
        payload = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        try:
            resp = requests.post(query_url, headers=headers,
                                 json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"查询数据库失败 (第{page_num}页): {e}")
            break

        results = data.get('results', [])
        logger.info(f"📥 第 {page_num} 页: {len(results)} 条记录")

        for page in results:
            page_id = page['id']

            # 提取所有属性 (表格列)
            record = {
                '_page_id': page_id,
                '_created_time': page.get('created_time', ''),
                '_last_edited': page.get('last_edited_time', ''),
            }

            for name, prop in page.get('properties', {}).items():
                record[name] = extract_property_value(prop)

            # 提取页面富文本内容 (交易总结)
            if fetch_content:
                content = extract_page_content(page_id, headers)
                record['_full_text'] = content['full_text']
                record['_image_urls'] = content['image_urls']

                # 下载截图
                if download_images and content['image_urls']:
                    # 用日期+代码作为子目录
                    trade_id = str(record.get('交易ID', '') or record.get('_page_id', '')[:8])
                    safe_id = trade_id.replace('/', '_').replace('\\', '_')
                    img_dir = os.path.join(ATTACHMENTS_DIR, safe_id)

                    local_paths = []
                    for i, img_url in enumerate(content['image_urls']):
                        ext = 'png'
                        fname = f"{safe_id}_img{i+1}.{ext}"
                        path = download_image(img_url, img_dir, fname)
                        if path:
                            local_paths.append(path)

                    record['_local_images'] = local_paths

            all_records.append(record)

        has_more = data.get('has_more', False)
        start_cursor = data.get('next_cursor')

    logger.info(f"✅ 同步完成: 共 {len(all_records)} 条记录")
    return all_records


# =====================================================================
# 写入本地 (JSON 存档 + SQLite)
# =====================================================================
def save_to_json(records: list, output_path: str = None):
    """将同步结果保存为 JSON (完整存档, 保留所有字段)"""
    if not output_path:
        output_path = os.path.join(project_root, "data", "notion_pa_journal.json")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 序列化处理 (有些值可能是 list)
    serializable = []
    for r in records:
        row = {}
        for k, v in r.items():
            if isinstance(v, list):
                row[k] = json.dumps(v, ensure_ascii=False) if v else ''
            elif v is None:
                row[k] = ''
            else:
                row[k] = v
        serializable.append(row)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2, default=str)

    logger.info(f"📄 JSON 存档保存至: {output_path}")
    return output_path


def save_to_review_db(records: list) -> dict:
    """
    将同步结果写入 trade_reviews 表。

    字段映射基于 2026-03-13 --discover 探测的真实 Notion PA日志 表头:
      交易ID(title), 品种(select), 方向(select), 入场价(number),
      初始止损(number), 初始止盈(number), 入场状态(State)(select),
      持续偏向(Always-In)(select), 结果(select), 最终R(number),
      复盘报告(rich_text), 备注(rich_text), 形态标签(multi_select),
      订单类型(select), 截图(files), 是否正确的交易(select),
      开仓时间(date), 平仓时间(date), 平仓均价(number),
      形态组合(formula), 入场周期(select), 结构周期(select)
    """
    from core.review_bridge import add_review

    stats = {'imported': 0, 'skipped': 0, 'filtered': 0, 'errors': 0}

    for r in records:
        try:
            # ── 核心标识: 品种 (Notion 用 "品种" 而非 "标的") ──
            code = r.get('品种', r.get('标的', r.get('code', '')))
            if not code:
                stats['skipped'] += 1
                continue

            # ── 策略过滤: 只导入匹配系统策略的交易 ──
            matched_strategy = _match_system_strategy(r)
            if not matched_strategy:
                trade_id = r.get('交易ID', code)
                logger.info(f"  🔇 FILTERED: {trade_id} (无匹配系统策略, 形态: {r.get('形态标签', 'N/A')})")
                stats['filtered'] += 1
                continue

            # ── 日期: 从开仓时间提取, 回退到创建时间 ──
            trade_date = r.get('开仓时间', r.get('日期', r.get('trade_date', r.get('_created_time', ''))))
            if isinstance(trade_date, str) and 'T' in trade_date:
                trade_date = trade_date.split('T')[0]
            # 处理 Notion date 的 "start ~ end" 格式
            if isinstance(trade_date, str) and '~' in trade_date:
                trade_date = trade_date.split('~')[0].strip()
            if not trade_date:
                trade_date = datetime.now().strftime('%Y-%m-%d')

            # ── 方向: "做多"/"做空" → LONG/SHORT ──
            direction = r.get('方向', r.get('direction', 'LONG'))
            if isinstance(direction, str):
                direction = direction.upper()
                if '多' in direction or 'LONG' in direction:
                    direction = 'LONG'
                elif '空' in direction or 'SHORT' in direction:
                    direction = 'SHORT'
                elif '跳' in direction or 'SKIP' in direction:
                    direction = 'SKIP'

            # ── 结果映射: "盈利"/"亏损" → WIN/LOSS ──
            result_raw = r.get('结果', r.get('Result', r.get('result', '')))
            if isinstance(result_raw, str):
                if '盈' in result_raw or 'WIN' in result_raw.upper():
                    result_mapped = 'WIN'
                elif '亏' in result_raw or 'LOSS' in result_raw.upper():
                    result_mapped = 'LOSS'
                elif '平' in result_raw or 'BREAK' in result_raw.upper():
                    result_mapped = 'BREAKEVEN'
                else:
                    result_mapped = result_raw
            else:
                result_mapped = str(result_raw) if result_raw else ''

            # ── Always-In 映射: "持续偏多" → LONG ──
            always_in_raw = r.get('持续偏向（Always-In）', r.get('Always-in', r.get('always_in_dir', '')))
            if isinstance(always_in_raw, str):
                if '多' in always_in_raw or 'LONG' in always_in_raw.upper():
                    always_in_mapped = 'LONG'
                elif '空' in always_in_raw or 'SHORT' in always_in_raw.upper():
                    always_in_mapped = 'SHORT'
                elif '中' in always_in_raw or 'NEUTRAL' in always_in_raw.upper():
                    always_in_mapped = 'NEUTRAL'
                else:
                    always_in_mapped = always_in_raw
            else:
                always_in_mapped = ''

            # ── 市场状态映射: "状态3（交易区间）" → STATE_3 ──
            state_raw = r.get('入场状态(State)', r.get('大周期状态', r.get('market_state', '')))
            if isinstance(state_raw, str):
                # 尝试提取 "状态N" 中的数字
                import re
                state_num = re.search(r'状态(\d+)', state_raw)
                if state_num:
                    state_mapped = f"STATE_{state_num.group(1)}"
                elif 'STATE' in state_raw.upper():
                    state_mapped = state_raw.upper()
                else:
                    state_mapped = state_raw  # 原样保留
            else:
                state_mapped = ''

            # ── 是否正确: "是"/"否" → 写入 is_correct ──
            is_correct = r.get('是否正确的交易', '')
            if isinstance(is_correct, str):
                if '是' in is_correct or 'YES' in is_correct.upper():
                    is_correct = 'YES'
                elif '否' in is_correct or 'NO' in is_correct.upper():
                    is_correct = 'NO'
                else:
                    is_correct = is_correct
            else:
                is_correct = ''

            # ── 订单类型映射: "买入止损单（Buy Stop）" → BUY_STOP ──
            order_raw = r.get('订单类型', r.get('入场类型', r.get('order_type', '')))
            if isinstance(order_raw, str):
                if 'BUY STOP' in order_raw.upper() or '买入止损' in order_raw:
                    order_mapped = 'BUY_STOP'
                elif 'SELL STOP' in order_raw.upper() or '卖出止损' in order_raw:
                    order_mapped = 'SELL_STOP'
                elif 'LIMIT' in order_raw.upper() or '限价' in order_raw:
                    order_mapped = 'LIMIT'
                elif 'MARKET' in order_raw.upper() or '市价' in order_raw:
                    order_mapped = 'MARKET'
                else:
                    order_mapped = order_raw
            else:
                order_mapped = ''

            # ── 安全浮点转换 ──
            def safe_float(val, default=0):
                if isinstance(val, (int, float)):
                    return float(val)
                if isinstance(val, str):
                    try:
                        return float(val.replace(',', ''))
                    except (ValueError, TypeError):
                        return default
                return default

            # ── 推断市场类型 (Notion 无 "市场" 字段, 从品种推断) ──
            market = _infer_market(code, r.get('市场', r.get('market', '')))

            # ── 形态标签: multi_select → pattern_tags ──
            pattern_tags = r.get('形态标签', r.get('pattern_tags', ''))
            # 形态组合: formula 计算字段
            pattern_combo = r.get('形态组合', r.get('pattern_combo', ''))

            # ── 时间周期 ──
            entry_tf = r.get('入场周期', r.get('entry_tf', ''))
            structure_tf = r.get('结构周期', r.get('structure_tf', ''))

            # ── 平仓时间 ──
            close_time = r.get('平仓时间', r.get('close_time', ''))
            if isinstance(close_time, str) and '~' in close_time:
                close_time = close_time.split('~')[0].strip()

            # ── 开仓时间 ──
            open_time = r.get('开仓时间', r.get('open_time', ''))
            if isinstance(open_time, str) and '~' in open_time:
                open_time = open_time.split('~')[0].strip()

            rid = add_review(
                code=code,
                trade_date=trade_date,
                direction=direction,
                market=market,
                strategy=matched_strategy,
                # 阶段1: 结构扫描
                market_state=state_mapped,
                structure_tf=structure_tf,
                key_levels=r.get('关键价位', r.get('key_levels', '')),
                vacuum_check=r.get('真空检查', r.get('vacuum_check', '')),
                # 阶段2: 信号触发
                entry_tf=entry_tf,
                signal_bar_note=r.get('入场K', r.get('signal_bar_note', '')),
                micro_pattern=r.get('微观形态', r.get('micro_pattern', '')),
                pattern_tags=pattern_tags,
                pattern_combo=pattern_combo,
                momentum_type=r.get('动能', r.get('momentum_type', '')),
                # 阶段3: 过滤器
                always_in_dir=always_in_mapped,
                trap_check=r.get('陷阱检查', r.get('trap_check', '')),
                planned_rr=safe_float(r.get('盈亏比', r.get('planned_rr', 0))),
                # 阶段4: 执行
                order_type=order_mapped,
                entry_price=safe_float(r.get('入场价', r.get('入场', r.get('entry_price', 0)))),
                sl_price=safe_float(r.get('初始止损', r.get('止损', r.get('sl_price', 0)))),
                tp_price=safe_float(r.get('初始止盈', r.get('目标', r.get('tp_price', 0)))),
                open_time=open_time,
                # 阶段5: 持仓管理
                exit_price=safe_float(r.get('平仓均价', r.get('平仓', r.get('exit_price', 0)))),
                exit_type=r.get('离场类型', r.get('exit_type', '')),
                result=result_mapped,
                final_r=safe_float(r.get('最终R', r.get('R值', r.get('final_r', 0)))),
                close_time=close_time,
                is_correct=is_correct,
                # 元数据
                context_tag=r.get('语境', r.get('context_tag', '')),
                entry_reason=r.get('入场理由', r.get('entry_reason', '')),
                skip_reason=r.get('跳过理由', r.get('skip_reason', '')),
                execution_score=int(safe_float(r.get('执行评分', r.get('execution_score', 0)))),
                lesson_tag=r.get('经验标签', r.get('lesson_tag', '')),
                review_report=str(r.get('复盘报告', ''))[:2000],  # 截断过长的报告
                # 将备注 + 富文本 + 图片路径合并到 notes
                notes=_build_notes(r),
                signal_id=str(r.get('交易ID', r.get('signal_id', ''))),
            )

            if rid:
                stats['imported'] += 1
                logger.info(f"  ✅ {code} | {trade_date} | {direction} | {result_mapped} | R={safe_float(r.get('最终R', 0)):+.2f}")
            else:
                stats['skipped'] += 1

        except Exception as e:
            logger.warning(f"导入失败 [{r.get('品种', r.get('交易ID', '?'))}]: {e}")
            stats['errors'] += 1

    logger.info(f"📊 写入 trade_reviews: "
                f"成功 {stats['imported']} | 过滤 {stats['filtered']} | 跳过 {stats['skipped']} | 错误 {stats['errors']}")
    return stats


# =====================================================================
# 策略匹配器: 只导入系统内有对应策略的 Notion 复盘记录
# =====================================================================
# 支持的系统策略关键词映射 (可扩展)
STRATEGY_KEYWORD_MAP = {
    'STRUCTURAL_GAP': ['gap', '缺口', 'measuring gap', '测算移动', 'mm（测算移动）',
                       'breakaway gap', '脱离缺口', 'exhaustion gap', '衰竭缺口'],
    'MTR':            ['mtr', 'major trend reversal', '趋势反转', 'higher low',
                       'hl', 'double bottom', '双底', '二次测试'],
    '3K':             ['3k', 'three k', '三k', '三根k线'],
}


def _match_system_strategy(record: dict) -> str:
    """
    根据 Notion 复盘记录的形态标签、形态组合、备注、复盘报告等字段,
    匹配系统内的策略名称。

    Returns:
        匹配到的策略名 (如 'STRUCTURAL_GAP'), 无匹配返回 ''
    """
    # 合并所有可能包含策略线索的文本字段
    search_fields = [
        str(record.get('形态标签', '')),
        str(record.get('形态组合', '')),
        str(record.get('备注', '')),
        str(record.get('复盘报告', '')),
        str(record.get('_full_text', '')),
        str(record.get('策略', '')),
    ]
    combined_text = ' '.join(search_fields).lower()

    # 按优先级匹配 (Gap 优先, 因为当前是聚焦策略)
    for strategy_name, keywords in STRATEGY_KEYWORD_MAP.items():
        for kw in keywords:
            if kw in combined_text:
                return strategy_name

    return ''  # 无匹配


def _infer_market(code: str, fallback: str = '') -> str:
    """
    根据品种代码推断市场类型。

    - ETH/USD, BTC/USD, SOL/USD → CRYPTO
    - ES, NQ, MES, MNQ, CL, GC → FUTURES
    - sh.XXXXXX, sz.XXXXXX → CN
    - 其他 → 使用 fallback 或默认 OTHER
    """
    code_upper = code.upper()

    # 加密货币
    if '/' in code_upper and ('USD' in code_upper or 'USDT' in code_upper):
        return 'CRYPTO'
    if code_upper in ('BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE'):
        return 'CRYPTO'

    # A 股
    if code_upper.startswith(('SH.', 'SZ.', '6', '0', '3')):
        return 'CN'

    # 美股期货
    if code_upper in ('ES', 'NQ', 'MES', 'MNQ', 'CL', 'GC', 'SI', 'RTY', 'YM', 'NKD'):
        return 'FUTURES'

    # 港股
    if code_upper.startswith('HK.') or code_upper.endswith('.HK'):
        return 'HK'

    return fallback.upper() if fallback else 'OTHER'


def _build_notes(record: dict) -> str:
    """将复盘备注、富文本内容和图片路径合并为 notes 字符串"""
    parts = []

    # 原始备注字段 (Notion 的 "备注" rich_text)
    raw_notes = record.get('备注', record.get('notes', ''))
    if raw_notes:
        parts.append(str(raw_notes))

    # 复盘报告 (Notion 的 "复盘报告" rich_text, 区别于 notes)
    review_report = record.get('复盘报告', '')
    if review_report:
        parts.append(f"\n--- 复盘报告 ---\n{str(review_report)[:2000]}")

    # 页面富文本 (Block Children 内容)
    full_text = record.get('_full_text', '')
    if full_text:
        parts.append(f"\n--- 页面内容 ---\n{full_text}")

    # 本地图片路径
    local_images = record.get('_local_images', [])
    if local_images:
        parts.append(f"\n--- 附件 ({len(local_images)}张截图) ---")
        for p in local_images:
            parts.append(f"  📷 {p}")

    return '\n'.join(parts) if parts else ''


# =====================================================================
# CLI 入口
# =====================================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='Notion PA日志 同步工具')
    parser.add_argument('--discover', action='store_true',
                        help='仅探测数据库结构, 不拉取数据')
    parser.add_argument('--no-content', action='store_true',
                        help='不提取页面内容 (只拉属性列)')
    parser.add_argument('--no-images', action='store_true',
                        help='不下载截图')
    parser.add_argument('--json-only', action='store_true',
                        help='只保存 JSON, 不写入 trade_reviews')
    parser.add_argument('--token', type=str, default='',
                        help='Notion API Token (默认从 .env 读取)')
    parser.add_argument('--db-id', type=str, default='',
                        help='Notion Database ID (默认从 .env 读取)')
    args = parser.parse_args()

    token = args.token or NOTION_TOKEN
    db_id = args.db_id or NOTION_DB_ID

    if not token:
        print("❌ 缺少 NOTION_TOKEN! 请在 .env 中配置 NOTION_TOKEN=your_token")
        print("\n  配置步骤:")
        print("  1. 打开 https://www.notion.so/my-integrations")
        print("  2. 点击 '+ New integration'")
        print("  3. 取一个名字 (如 BrooksAI), 选择你的工作区")
        print("  4. 权限: 勾选 'Read content'(读取内容)")
        print("  5. 复制 Integration Token")
        print("  6. 在 .env 中添加: NOTION_TOKEN=secret_xxxxxxxxx")
        print("  7. 回到 Notion, 打开你的 PA日志数据库")
        print("  8. 点击右上角 ··· → Connections → 添加你刚创建的 Integration")
        print("  9. 复制数据库ID (URL中 notion.so/ 后面那串32位字符)")
        print("  10. 在 .env 中添加: NOTION_DB_ID=xxxxxxxx")
        return

    if not db_id:
        print("❌ 缺少 NOTION_DB_ID! 请在 .env 中配置 NOTION_DB_ID=your_database_id")
        print("  数据库ID在URL中: https://notion.so/[这一串就是ID]?v=...")
        return

    # 模式1: 探测结构
    if args.discover:
        print("🔍 探测 Notion 数据库结构...\n")
        info = discover_database(token, db_id)

        if not info:
            print("❌ 探测失败, 请检查 Token 和 Database ID")
            return

        print(f"📚 数据库: {info.get('title', '未知')}")
        print(f"📊 共 {info['total_props']} 个属性列:\n")

        print(f"  {'属性名':<20} {'类型':<15}")
        print(f"  {'─' * 20} {'─' * 15}")
        for name, ptype in sorted(info['properties'].items()):
            print(f"  {name:<20} {ptype:<15}")

        sample = info.get('sample_row', {})
        if sample:
            print(f"\n📋 样本数据 (第1条):")
            for name, val in sample.items():
                val_str = str(val)[:80]
                print(f"  {name}: {val_str}")

        return

    # 模式2: 全量同步
    print(f"🚀 开始同步 Notion PA日志...")
    print(f"   内容提取: {'✅' if not args.no_content else '❌'}")
    print(f"   截图下载: {'✅' if not args.no_images else '❌'}")
    print()

    records = sync_all(
        token, db_id,
        fetch_content=not args.no_content,
        download_images=not args.no_images
    )

    if not records:
        print("❌ 未获取到任何记录")
        return

    # 保存为 JSON (完整存档)
    json_path = save_to_json(records)

    # 写入 trade_reviews 表
    if not args.json_only:
        stats = save_to_review_db(records)
        print(f"\n✅ 同步完成!")
        print(f"   JSON 存档: {json_path}")
        print(f"   数据库写入: 成功 {stats['imported']} | 过滤 {stats['filtered']} | 跳过 {stats['skipped']} | 错误 {stats['errors']}")
    else:
        print(f"\n✅ JSON 存档已保存: {json_path}")


if __name__ == '__main__':
    main()
