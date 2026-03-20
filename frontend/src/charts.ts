import { Chart, registerables } from "chart.js";
import type { MonthlySummary, CategorySummary, TrendPoint } from "./api";

Chart.register(...registerables);

const COLORS = [
  "#4dc9f6", "#f67019", "#f53794", "#537bc4", "#acc236",
  "#166a8f", "#00a950", "#58595b", "#8549ba", "#e6194b",
  "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4",
];

function fmtDollar(v: number): string {
  return "$" + v.toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtDollarShort(v: number): string {
  return "$" + Number(v).toLocaleString("en-AU");
}

let monthlyChart: Chart | null = null;
let categoryChart: Chart | null = null;
let trendsChart: Chart | null = null;

export function renderMonthlyChart(canvas: HTMLCanvasElement, data: MonthlySummary[]) {
  if (monthlyChart) monthlyChart.destroy();

  monthlyChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: data.map((d) => d.month),
      datasets: [
        {
          label: "Income",
          data: data.map((d) => d.income),
          borderColor: "#00a950",
          backgroundColor: "rgba(0, 169, 80, 0.15)",
          fill: true,
          tension: 0.3,
          pointRadius: 3,
        },
        {
          label: "Expenses",
          data: data.map((d) => Math.abs(d.expenses)),
          borderColor: "#f53794",
          backgroundColor: "rgba(245, 55, 148, 0.15)",
          fill: true,
          tension: 0.3,
          pointRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: "top" },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${fmtDollar(Number(ctx.raw))}`,
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { callback: (v) => fmtDollarShort(Number(v)) },
        },
      },
    },
  });
}

export function renderCategoryChart(canvas: HTMLCanvasElement, data: CategorySummary[]) {
  if (categoryChart) categoryChart.destroy();

  // Only show expenses (negative totals)
  const expenses = data.filter((d) => d.total < 0 && d.category !== "Uncategorized");

  categoryChart = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: expenses.map((d) => d.category),
      datasets: [
        {
          data: expenses.map((d) => Math.abs(d.total)),
          backgroundColor: COLORS.slice(0, expenses.length),
        },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: "right" },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${ctx.label}: ${fmtDollar(Number(ctx.raw))}`,
          },
        },
      },
    },
  });
}

export function renderTrendsChart(canvas: HTMLCanvasElement, data: TrendPoint[]) {
  if (trendsChart) trendsChart.destroy();

  // Group by category
  const categories = [...new Set(data.map((d) => d.category))];
  const months = [...new Set(data.map((d) => d.month))].sort();
  const lookup = new Map(data.map((d) => [`${d.month}|${d.category}`, d.total]));

  trendsChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: months,
      datasets: categories.map((cat, i) => ({
        label: cat || "Uncategorized",
        data: months.map((m) => Math.abs(lookup.get(`${m}|${cat}`) || 0)),
        borderColor: COLORS[i % COLORS.length],
        backgroundColor: "transparent",
        tension: 0.3,
      })),
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: "top" },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${fmtDollar(Number(ctx.raw))}`,
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { callback: (v) => fmtDollarShort(Number(v)) },
        },
      },
    },
  });
}
