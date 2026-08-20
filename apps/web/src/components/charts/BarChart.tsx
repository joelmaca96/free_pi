import ReactECharts from "echarts-for-react";

export interface BarSeries {
  name: string;
  values: (number | null)[];
}

/** Wrapper ECharts equivalente a `st.bar_chart` (usado en Resumen: ORtg/DRtg y PTS por jugador). */
export function BarChart({
  categories,
  series,
  height = 240,
}: {
  categories: string[];
  series: BarSeries[];
  height?: number;
}) {
  const option = {
    grid: { left: 48, right: 16, top: 24, bottom: 48 },
    tooltip: { trigger: "axis" },
    legend: series.length > 1 ? { top: 0 } : undefined,
    xAxis: {
      type: "category",
      data: categories,
      axisLabel: { rotate: categories.length > 6 ? 45 : 0, fontSize: 10 },
    },
    yAxis: { type: "value" },
    series: series.map((s) => ({
      name: s.name,
      type: "bar",
      data: s.values,
    })),
  };

  return <ReactECharts option={option} style={{ height }} notMerge />;
}
