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
  const palette = ["#9184d9", "#9397ab", "#e11d48"];
  const textColor = "#e9e9ed";
  const mutedColor = "#9397ab";
  const gridColor = "rgba(233, 233, 237, 0.16)";

  const option = {
    color: palette,
    textStyle: { fontFamily: "Inter, system-ui, sans-serif", color: textColor },
    grid: { left: 48, right: 16, top: 24, bottom: 48 },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#232532",
      borderColor: gridColor,
      textStyle: { color: textColor },
    },
    legend: series.length > 1 ? { top: 0, textStyle: { color: textColor } } : undefined,
    xAxis: {
      type: "category",
      data: categories,
      axisLabel: { rotate: categories.length > 6 ? 45 : 0, fontSize: 10, color: mutedColor },
      axisLine: { lineStyle: { color: gridColor } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: mutedColor },
      splitLine: { lineStyle: { color: gridColor } },
    },
    series: series.map((s) => ({
      name: s.name,
      type: "bar",
      data: s.values,
    })),
  };

  return <ReactECharts option={option} style={{ height }} notMerge />;
}
