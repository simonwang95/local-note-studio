export type ManifestCardState = { open: boolean; filter: string };

export class ManifestViewStateStore {
  private readonly cards = new Map<string, ManifestCardState>();

  get(path: string): ManifestCardState | undefined {
    return this.cards.get(path);
  }

  remember(path: string, open: boolean, filter: string): void {
    if (!path) return;
    this.cards.set(path, { open, filter: filter || "all" });
  }

  keepOpen(path: string, filter: string): void {
    this.remember(path, true, filter);
  }
}
