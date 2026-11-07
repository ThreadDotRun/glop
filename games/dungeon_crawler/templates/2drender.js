// dev/glop/games/dungeon_crawler/templates/2drender.js
class Renderer2D {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');

    this.dpr = window.devicePixelRatio || 1;
    this.cssWidth = this.canvas.width;
    this.cssHeight = this.canvas.height;
    this.canvas.width = Math.floor(this.cssWidth * this.dpr);
    this.canvas.height = Math.floor(this.cssHeight * this.dpr);
    this.canvas.style.width = this.cssWidth + 'px';
    this.canvas.style.height = this.cssHeight + 'px';
    this.ctx.scale(this.dpr, this.dpr);

    this.colors = {
      floor: '#1a2a1a', floorLine: '#2a3a2a',
      player: '#0033ff', hostile: '#ff2222',
      city: '#ffd700', fallback: '#555'
    };
    this.gridW = 10;
    this.gridH = 10;
    this.padding = 10;
    this.tileSize = 0;
  }

  getTileFromClick(cx, cy) {
    const x = Math.floor((cx - this.padding) / this.tileSize);
    const y = Math.floor((cy - this.padding) / this.tileSize);
    if (x >= 0 && x < this.gridW && y >= 0 && y < this.gridH) {
      return y * this.gridW + x;
    }
    return null;
  }

  update(state) {
    this._lastState = state;
    const activeUnitId = state.active_unit;
    const W = this.cssWidth;
    const H = this.cssHeight;
    this.ctx.fillStyle = '#050505';
    this.ctx.fillRect(0, 0, W, H);

    const bounds = state.container_bounds || { min: [0, 0], max: [10, 10] };
    this.gridW = bounds.max[0] - bounds.min[0];
    this.gridH = bounds.max[1] - bounds.min[1];

    const availW = (W - this.padding * 2) / this.gridW;
    const availH = (H - this.padding * 2) / this.gridH;
    this.tileSize = Math.min(availW, availH);

    for (let x = 0; x < this.gridW; x++) {
      for (let y = 0; y < this.gridH; y++) {
        this.ctx.fillStyle = this.colors.floor;
        this.ctx.fillRect(this.padding + x * this.tileSize,
                          this.padding + y * this.tileSize,
                          this.tileSize, this.tileSize);
        this.ctx.strokeStyle = this.colors.floorLine;
        this.ctx.strokeRect(this.padding + x * this.tileSize,
                            this.padding + y * this.tileSize,
                            this.tileSize, this.tileSize);
      }
    }

    const byTile = {};
    for (const e of state.entities) {
      const tile = e.pos && e.pos.tile;
      if (tile === undefined || tile === 99) continue;
      if (e.kind === 'environment') continue;
      if (e.hp !== null && e.hp !== undefined && e.hp <= 0) continue;
      (byTile[tile] = byTile[tile] || []).push(e);
    }

    for (const tileStr of Object.keys(byTile)) {
      const tile = parseInt(tileStr, 10);
      const ents = byTile[tile];
      const x = tile % this.gridW;
      const y = Math.floor(tile / this.gridW);
      const px = this.padding + x * this.tileSize;
      const py = this.padding + y * this.tileSize;
      const stacked = ents.length > 1;
      const drawSize = stacked ? this.tileSize * 0.7 : this.tileSize;
      const step = drawSize * 0.15;

      for (let i = 0; i < ents.length; i++) {
        const e = ents[i];
        const ox = stacked ? (i % 2 === 0 ? step : step * 2) : drawSize * 0.05;
        const oy = stacked ? (i >= 2 ? step * 2 : step) : drawSize * 0.05;

        let color = this.colors.fallback;
        let letter = (e.name || '?').charAt(0).toUpperCase();
        if (e.tags.includes('roman')) {
          color = this.colors.player;
          if (e.kind === 'city') { letter = '★'; color = this.colors.city; }
        } else if (e.tags.includes('aztec')) {
          color = this.colors.hostile;
          if (e.kind === 'city') { letter = '★'; color = this.colors.city; }
        }

        const bx = px + ox, by = py + oy;
        const bw = drawSize - step * 2, bh = drawSize - step * 2;
        this.ctx.fillStyle = color;
        this.ctx.fillRect(bx, by, bw, bh);

        if (e.id === activeUnitId) {
          this.ctx.shadowColor = '#ffd700';
          this.ctx.shadowBlur = 10;
          this.ctx.strokeStyle = '#ffd700';
          this.ctx.lineWidth = 4;
          this.ctx.strokeRect(bx - 2, by - 2, bw + 2, bh + 2);
          this.ctx.shadowBlur = 0;
          this.ctx.lineWidth = 1;
        }

        this.ctx.fillStyle = '#fff';
        this.ctx.font = `${Math.floor(bw * 0.6)}px monospace`;
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = 'middle';
        this.ctx.fillText(letter, bx + bw / 2, by + bh / 2);
      }
    }
  }
}
