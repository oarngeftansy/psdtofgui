import { useState } from "react";
import type { HifiProjectTree, HifiTargetRef, ProjectView } from "../../../figma-plugin/src/project-client";

export type HifiTargetSelection = readonly [number, number, number];

function reasonLabel(reason?: string): string {
  const labels: Record<string, string> = {
    unsupported_fairygui_version: "仅支持 FairyGUI 6.1.4",
    component_unavailable: "组件文件不可读",
    invalid_component_root: "不是有效根组件",
    no_selectable_components: "没有可选组件",
  };
  return reason ? labels[reason] ?? "不可选择" : "不可选择";
}

function sameSelection(left: HifiTargetSelection, right: HifiTargetSelection): boolean {
  return left[0] === right[0] && left[1] === right[1] && left[2] === right[2];
}

export function selectedHifiTarget(
  project: ProjectView,
  tree: HifiProjectTree,
  selected?: HifiTargetSelection,
): HifiTargetRef | undefined {
  if (!selected) return undefined;
  const [packageIndex, directoryIndex, componentIndex] = selected;
  const packageItem = tree.packages[packageIndex];
  const directory = packageItem?.directories[directoryIndex];
  const component = directory?.components[componentIndex];
  if (!packageItem || !directory || !component?.selectable) return undefined;
  return {
    version: 1,
    projectId: project.projectId,
    projectFingerprint: tree.projectFingerprint,
    packageId: packageItem.packageId,
    packageName: packageItem.name,
    directory: directory.path,
    componentId: component.resourceId,
    componentName: component.name,
    componentRelativePath: component.relativePath,
  };
}

export function HifiTargetPicker({
  project,
  tree,
  value,
  values,
  disabled = false,
  onChange,
  onToggle,
}: {
  project: ProjectView;
  tree: HifiProjectTree;
  value?: HifiTargetSelection;
  values?: readonly HifiTargetSelection[];
  disabled?: boolean;
  onChange?(value: HifiTargetSelection): void;
  onToggle?(value: HifiTargetSelection): void;
}) {
  const multiple = Boolean(onToggle);
  const picked = values ?? (value ? [value] : []);
  const isActive = (selection: HifiTargetSelection) => picked.some((item) => sameSelection(item, selection));
  const summaryTargets = picked
    .map((selection) => selectedHifiTarget(project, tree, selection))
    .filter((item): item is HifiTargetRef => Boolean(item));
  const [expandedPackage, setExpandedPackage] = useState<number>();
  const [expandedDirectory, setExpandedDirectory] = useState<string>();
  return <section className="hifi-target-tree" aria-labelledby="hifi-target-title">
    <div className="hifi-section-heading">
      <div><h2 id="hifi-target-title">FGUI 修改位置</h2><p>{multiple ? "可勾选多个根组件，一次建多个替换会话。" : "选择目录，再选择该目录中的根组件。"}</p></div>
      <span>{multiple ? `已选 ${summaryTargets.length}` : "需确认"}</span>
    </div>
    {tree.packages.map((packageItem, packageIndex) => {
      const packageExpanded = expandedPackage === packageIndex;
      return <div className="hifi-package" key={packageItem.packageId}>
      <button className="hifi-package-toggle" type="button" aria-expanded={packageExpanded} disabled={disabled} onClick={() => { setExpandedPackage(packageExpanded ? undefined : packageIndex); setExpandedDirectory(undefined); }}><span>{packageExpanded ? "▾" : "▸"} {packageItem.name}</span><small>{packageItem.directories.length} 个目录</small></button>
      {packageExpanded && packageItem.directories.map((directory, directoryIndex) => {
        const directoryKey = `${packageIndex}:${directoryIndex}`;
        const expanded = expandedDirectory === directoryKey;
        const directoryActive = !multiple && value?.[0] === packageIndex && value[1] === directoryIndex;
        return <div key={directory.path}>
        <button
          className={`hifi-tree-row hifi-directory ${directoryActive ? "is-selected" : ""}`}
          type="button"
          aria-pressed={directoryActive}
          aria-expanded={expanded}
          disabled={disabled || !directory.selectable}
          onClick={() => { setExpandedDirectory(expanded ? undefined : directoryKey); if (!multiple) onChange?.([packageIndex, directoryIndex, -1]); }}
        ><span>{expanded ? "▾" : "▸"} {directory.path}</span><small>{directory.selectable ? `${directory.components.length} 个组件` : reasonLabel(directory.reason)}</small></button>
        {expanded && directory.components.map((component, componentIndex) => {
          const selection: HifiTargetSelection = [packageIndex, directoryIndex, componentIndex];
          const active = isActive(selection);
          return <button
            className={`hifi-tree-row hifi-component ${active ? "is-selected" : ""}`}
            type="button"
            aria-pressed={active}
            disabled={disabled || !component.selectable}
            onClick={() => (multiple ? onToggle?.(selection) : onChange?.(selection))}
            key={component.resourceId}
          ><span>{multiple ? `${active ? "☑" : "☐"} ${component.name}` : `◆ ${component.name}`}</span><small>{component.selectable ? "根组件" : reasonLabel(component.reason)}</small></button>;
        })}
      </div>;})}
    </div>;})}
    {summaryTargets.length > 0 && <dl className="hifi-target-summary">
      <div><dt>{summaryTargets.length > 1 ? `已选 ${summaryTargets.length} 个根组件` : "目标目录"}</dt><dd>{summaryTargets.length > 1 ? summaryTargets.map((item) => `${item.packageName} / ${item.directory} / ${item.componentName}`).join("；") : `${summaryTargets[0].packageName} / ${summaryTargets[0].directory}`}</dd></div>
      {summaryTargets.length === 1 && <div><dt>根组件</dt><dd>{summaryTargets[0].componentName}</dd></div>}
    </dl>}
  </section>;
}
