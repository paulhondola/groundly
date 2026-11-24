import type { SourceItem, TopicNode } from "../types";

function matchesQuery(node: TopicNode, needle: string): boolean {
  return node.title.toLowerCase().includes(needle) || node.summary.toLowerCase().includes(needle);
}

export function filterTree(tree: TopicNode[], query: string): TopicNode[] {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return tree;
  }
  const out: TopicNode[] = [];
  for (const topic of tree) {
    if (matchesQuery(topic, needle)) {
      out.push(topic);
      continue;
    }
    const matchingSubs = topic.subtopics.filter((sub) => matchesQuery(sub, needle));
    if (matchingSubs.length > 0) {
      out.push({ ...topic, subtopics: matchingSubs });
    }
  }
  return out;
}

export function findTopicById(tree: TopicNode[], id: number | null): TopicNode | null {
  if (id === null) {
    return null;
  }
  for (const topic of tree) {
    if (topic.id === id) {
      return topic;
    }
    for (const sub of topic.subtopics) {
      if (sub.id === id) {
        return sub;
      }
    }
  }
  return null;
}

export function writeButtonInfo(
  available: boolean,
  target: number,
  node: TopicNode,
): { label: string } | null {
  if (!available) {
    return null;
  }
  const leaves = node.subtopics.length > 0 ? node.subtopics : [node];
  if (!leaves.some((leaf) => (leaf.progress ? leaf.progress.questions : 0) < target)) {
    return null;
  }
  return {
    label: node.subtopics.length > 0 ? "Write questions for its subtopics" : "Write questions",
  };
}

export function dedupeSources(sources: SourceItem[]): SourceItem[] {
  const seen = new Set<string>();
  const out: SourceItem[] = [];
  for (const src of sources) {
    if (src.pdf === true) {
      const key = `${src.filename}\u0000${src.page}`;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
    }
    out.push(src);
  }
  return out;
}

// oxfmt-ignore
export function writePrompt(title: string, subjectName: string): string {
  return "Write questions on \"" + title + "\" for " + subjectName + ".";
}
