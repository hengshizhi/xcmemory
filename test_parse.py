import sys
sys.path.insert(0, 'src')
from xcmemory_interest.mql.parser import parse

mql = "SELECT * FROM memories WHERE subject='星织' GRAPH EXPAND(HOPS 2) LIMIT 20"
ast = parse(mql)
print('type:', type(ast).__name__)
print('graph_clause:', ast.graph_clause)
print('graph operation:', ast.graph_clause.operation if ast.graph_clause else None)
print('graph hops:', ast.graph_clause.hops if ast.graph_clause else None)
print('limit:', ast.limit)
