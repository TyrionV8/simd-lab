// Keep each source close to its first consumer, then pack measured cards by column.
export function arrangeNodes(nodes,edges,sizes) {
  const depths=new Map(),visiting=new Set();
  const depth=id=>{
    if(depths.has(id))return depths.get(id);
    if(visiting.has(id))return 0;
    visiting.add(id);
    const parents=edges.filter(edge=>edge.to===id).map(edge=>edge.from);
    const result=parents.length?Math.max(...parents.map(id=>depth(id)+1)):0;
    visiting.delete(id);depths.set(id,result);return result;
  };
  nodes.forEach(node=>depth(node.id));
  for(const node of nodes) {
    if(edges.some(edge=>edge.to===node.id))continue;
    const consumers=edges.filter(edge=>edge.from===node.id).map(edge=>depths.get(edge.to));
    if(consumers.length)depths.set(node.id,Math.min(...consumers)-1);
  }
  const columns=new Map();
  nodes.forEach(node=>{const column=depths.get(node.id);if(!columns.has(column))columns.set(column,[]);columns.get(column).push(node);});
  const positions=new Map();let x=24;
  for(const column of [...columns.keys()].sort((a,b)=>a-b)) {
    let y=24,width=0;
    const ordered=columns.get(column).toSorted((a,b)=>Number(!edges.some(edge=>edge.to===a.id))-Number(!edges.some(edge=>edge.to===b.id)));
    for(const node of ordered) {
      const size=sizes.get(node.id);positions.set(node.id,{x,y});
      y+=size.height+24;width=Math.max(width,size.width);
    }
    x+=width+150;
  }
  return positions;
}
