export function PlayMark({ size = 14, color = "#111" }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 14 14" aria-hidden="true">
      <path d="M3 2 L12 7 L3 12 Z" fill={color} />
    </svg>
  );
}

export function Brand({ big = false }: { big?: boolean }) {
  return (
    <div className="brand">
      <div className="brand-badge" style={big ? { width: 40, height: 40, borderRadius: 10 } : undefined}>
        <PlayMark size={big ? 18 : 14} />
      </div>
      <span className="brand-name" style={big ? { fontSize: 24 } : undefined}>ClipFarm</span>
    </div>
  );
}
