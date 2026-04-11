# -*- coding: utf-8 -*-
"""测试 Discord 消息分段逻辑"""
import sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.notifier import _split_message_by_lines

def test_short_message():
    """短消息不分段"""
    chunks = _split_message_by_lines('hello world', 1950)
    assert len(chunks) == 1
    print('✅ 测试1通过: 短消息不分段')

def test_long_message_split():
    """长消息按行智能分段, 不丢内容"""
    lines = []
    for i in range(30):
        lines.append(f'• 测试股票{i:02d} (sz.00{i:04d}) | 买入>=10.00 | 止损:8.50 | 止盈:15.00 | R:R=1:3.3 | 缺口=5.2%')
    long_msg = '\n'.join(lines)
    print(f'  总长度: {len(long_msg)} 字符')

    chunks = _split_message_by_lines(long_msg, 500)
    print(f'  分成 {len(chunks)} 段')
    for i, c in enumerate(chunks):
        print(f'    段{i+1}: {len(c)} 字符')
        assert len(c) <= 500, f'段{i+1}超长: {len(c)}'

    # 验证内容完整性
    reconstructed = '\n'.join(chunks)
    assert reconstructed == long_msg, '重组后内容应完全一致'
    print('✅ 测试2通过: 长消息智能分段, 内容无丢失')

def test_22_signals_no_truncation():
    """模拟22只标的的实际场景"""
    msg = '🔔 **周线扫描完成**\n'
    msg += '命中 22 只\n'
    msg += '---\n'
    for i in range(22):
        msg += f'• 股票{i+1} (sz.00{i:04d}) | 买入>=10.{i:02d} | 止损:8.{i:02d} | 止盈:15.{i:02d} | R:R=1:3.{i}\n'
    msg += '\n📈 图表即将推送...'

    print(f'\n  模拟22只消息总长度: {len(msg)} 字符')
    chunks = _split_message_by_lines(msg, 1950)
    print(f'  分成 {len(chunks)} 段发送')
    
    # 验证所有22只都在最终内容中
    full = '\n'.join(chunks)
    for i in range(22):
        assert f'股票{i+1}' in full, f'股票{i+1} 丢失！'
    
    for i, c in enumerate(chunks):
        assert len(c) <= 1950, f'段{i+1}超长: {len(c)}'
        print(f'    段{i+1}: {len(c)} 字符')
    print('✅ 测试3通过: 22只标的全量推送, 无截断')

if __name__ == '__main__':
    test_short_message()
    test_long_message_split()
    test_22_signals_no_truncation()
    print('\n🎉 全部测试通过!')
