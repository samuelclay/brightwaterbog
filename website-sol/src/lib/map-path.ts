export type MapPoint = { x: number; y: number };

const round = (value: number) => Number(value.toFixed(2));

export function smoothPath(points: MapPoint[]): string {
  if (!points.length) return "";
  if (points.length === 1) return `M ${round(points[0].x)} ${round(points[0].y)}`;
  if (points.length === 2) {
    return `M ${round(points[0].x)} ${round(points[0].y)} L ${round(points[1].x)} ${round(points[1].y)}`;
  }

  const commands = [`M ${round(points[0].x)} ${round(points[0].y)}`];
  for (let index = 1; index < points.length - 1; index += 1) {
    const point = points[index];
    const next = points[index + 1];
    const midpoint = { x: (point.x + next.x) / 2, y: (point.y + next.y) / 2 };
    commands.push(`Q ${round(point.x)} ${round(point.y)} ${round(midpoint.x)} ${round(midpoint.y)}`);
  }
  const last = points[points.length - 1]!;
  commands.push(`T ${round(last.x)} ${round(last.y)}`);
  return commands.join(" ");
}
