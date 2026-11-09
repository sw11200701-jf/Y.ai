'use client';
import { useEffect, useRef } from 'react';
import { Alert, Collapse, Table, Tag } from 'antd';
import * as echarts from 'echarts';
import { decimal, Json, ratioText } from '@/lib/api';
import { JsonView } from './shared';
function safeText(value: unknown) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] || c)); }
export default function DatasetResult({ dataset, chartSpec }: { dataset: Json; chartSpec?: Json }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current); const periods = dataset.query?.periods || [];
    // Chart coordinates are the only numeric conversion; tables/downloads retain decimal strings.
    const rows = (dataset.rows || []).filter((r: Json) => (chartSpec?.series || ['revenue', 'net_profit']).includes(r.metric_code));
    chart.setOption({
      color: ['#367e60', '#c2ac73'],
      tooltip: { trigger: 'axis', formatter: (params: unknown) => {
        const entries = (Array.isArray(params) ? params : [params]) as Json[];
        return safeText(entries[0]?.axisValueLabel) + entries.map(p => `<br/>${safeText(p.seriesName)}：${safeText(decimal(p.data?.raw_amount))} 元`).join('');
      } },
      legend: { bottom: 0, icon: 'roundRect', itemHeight: 8, textStyle: { color: '#74816d', fontSize: 11 } },
      grid: { left: 85, right: 20, top: 30, bottom: 60 },
      xAxis: { type: 'category', data: periods, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e4eadf' } }, axisLabel: { color: '#7a8b72' } },
      yAxis: { type: 'value', name: `${dataset.query?.currency || 'CNY'} / 元`, nameTextStyle: { color: '#8b9980', fontSize: 10 }, axisLabel: { color: '#82947a', fontSize: 10 }, splitLine: { lineStyle: { color: '#eef2e9', type: 'dashed' } } },
      series: rows.map((r: Json) => ({ name: r.label, type: 'bar', barMaxWidth: 46, itemStyle: { borderRadius: [4, 4, 0, 0] }, data: periods.map((p: string) => ({ value: r[p] == null ? null : Number(r[p]), raw_amount: r[p] })) }))
    });
    const observer = new ResizeObserver(() => chart.resize()); observer.observe(ref.current); return () => { observer.disconnect(); chart.dispose(); };
  }, [dataset, chartSpec]);
  const columns = (dataset.columns || []).map((c: Json) => ({ title: c.label || c.key, dataIndex: c.key, key: c.key, align: c.key === 'label' ? 'left' as const : 'right' as const, render: (v: unknown, r: Json) => c.key === 'label' ? <span>{String(v)} {r.unit === 'ratio' && <Tag bordered={false}>比率</Tag>}</span> : <span title={r.reasons?.[c.key] || ''}>{r.unit === 'ratio' ? ratioText(v) : decimal(v)}{v == null && r.reasons?.[c.key] ? <small className="subtle"> · {r.reasons[c.key] === 'ZERO_DENOMINATOR' ? '分母为零' : '缺少数据'}</small> : null}</span> }));
  return <><div className="meta-row"><span>数据集 <code>{dataset.id}</code></span><span>快照 <code title={dataset.snapshot_id}>{String(dataset.snapshot_id).slice(0, 25)}…</code></span><span>指标版本 {dataset.metric_version}</span><Tag color="gold">{dataset.synthetic ? '合成数据' : '业务数据'}</Tag></div>{dataset.warnings?.length > 0 && <Alert type="warning" showIcon message={`${dataset.warnings.length} 项口径提示：缺失值与零分母显示为“—”，不填零。`} description={<Collapse ghost size="small" items={[{ key: 'warnings', label: '查看具体指标和期间', children: <JsonView value={dataset.warnings} /> }]} />} />}<div className="two-column"><section className="panel"><div className="panel-heading"><h2><span className="section-number">01</span>年度指标对比</h2><small>金额单位：人民币元</small></div><Table rowKey="metric_code" size="middle" columns={columns} dataSource={dataset.rows || []} pagination={false} /><div className="data-note">同比 =（本期 − 上年）/ |上年|；净利率 = 净利润 / 营业收入；资产负债率 = 负债总额 / 资产总额。比率由服务端 Decimal 计算。</div></section><section className="panel"><div className="panel-heading"><h2><span className="section-number">02</span>收入与利润</h2><small>同一数据快照</small></div>{chartSpec && chartSpec.dataset_id !== dataset.id ? <Alert type="error" message="图表数据集引用不匹配，已停止绘图" /> : <div ref={ref} className="chart" />}<div className="data-note">图表仅转换绘图坐标；精确十进制金额见左侧表格。</div></section></div><section className="panel"><div className="panel-heading"><h2><span className="section-number">03</span>来源与可追溯性</h2><small>图表、表格及报告引用同一 snapshot_id</small></div>{(dataset.source_refs || []).map((s: string, i: number) => <div className="source-item" key={s}><Tag>{String(i + 1).padStart(2, '0')}</Tag>{s}</div>)}<Collapse ghost items={[{ key: 'policy', label: '查看完整数据快照与计算口径', children: <JsonView value={dataset} /> }]} /></section></>;
}
