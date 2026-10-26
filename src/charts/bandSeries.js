// A lightweight-charts v5 custom series that fills the area between two values per bar (an
// interval band). Data items: { time, lo, hi }; an item without both is whitespace (a gap).
import { customSeriesDefaultOptions } from "lightweight-charts";

class BandRenderer {
  constructor() {
    this.data = null;
    this.options = null;
  }

  update(data, options) {
    this.data = data;
    this.options = options;
  }

  draw(target, priceToCoordinate) {
    const data = this.data;
    if (!data || !data.visibleRange || data.bars.length === 0) return;
    target.useMediaCoordinateSpace(({ context }) => {
      const { from, to } = data.visibleRange;
      // The library computes x only for visible bars; the bar just outside each edge (drawn so the
      // band reaches the plot's edges) would keep a stale x from an earlier view. Every x is placed
      // from the first visible bar instead: `time` is the bar's logical index.
      const anchor = from < to ? data.bars[from] : null;
      if (!anchor || !Number.isFinite(anchor.x) || !Number.isFinite(data.barSpacing)) return;
      const xOf = (bar) => anchor.x + (bar.time - anchor.time) * data.barSpacing;
      // Draw each contiguous run of defined bars as its own polygon, so a gap stays a gap.
      let run = [];
      const flush = () => {
        if (run.length > 1) {
          context.beginPath();
          run.forEach((point, index) => (index === 0 ? context.moveTo(point.x, point.hi) : context.lineTo(point.x, point.hi)));
          for (let i = run.length - 1; i >= 0; i -= 1) context.lineTo(run[i].x, run[i].lo);
          context.closePath();
          context.fillStyle = this.options.fillColor;
          context.fill();
          if (this.options.edgeColor) {
            context.strokeStyle = this.options.edgeColor;
            context.lineWidth = 1;
            context.beginPath();
            run.forEach((point, index) => (index === 0 ? context.moveTo(point.x, point.hi) : context.lineTo(point.x, point.hi)));
            context.stroke();
            context.beginPath();
            run.forEach((point, index) => (index === 0 ? context.moveTo(point.x, point.lo) : context.lineTo(point.x, point.lo)));
            context.stroke();
          }
        }
        run = [];
      };
      const start = Math.max(0, from - 1);
      const end = Math.min(data.bars.length, to + 1);
      for (let i = start; i < end; i += 1) {
        const bar = data.bars[i];
        const { lo, hi } = bar.originalData ?? {};
        if (lo == null || hi == null) {
          flush();
          continue;
        }
        const yLo = priceToCoordinate(lo);
        const yHi = priceToCoordinate(hi);
        if (yLo == null || yHi == null) {
          flush();
          continue;
        }
        run.push({ x: xOf(bar), lo: yLo, hi: yHi });
      }
      flush();
    });
  }
}

export class BandSeries {
  constructor() {
    this.bandRenderer = new BandRenderer();
  }

  priceValueBuilder(plotRow) {
    return [plotRow.lo, plotRow.hi, (plotRow.lo + plotRow.hi) / 2];
  }

  isWhitespace(data) {
    return data.lo == null || data.hi == null;
  }

  renderer() {
    return this.bandRenderer;
  }

  update(data, options) {
    this.bandRenderer.update(data, options);
  }

  defaultOptions() {
    return {
      ...customSeriesDefaultOptions,
      fillColor: "rgba(57, 135, 229, 0.16)",
      edgeColor: null,
      lastValueVisible: false,
      priceLineVisible: false,
    };
  }
}
