import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from fwdis import *
a = int(sys.argv[1], 0); n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
dis(a, n, thumb=len(sys.argv) > 3 and sys.argv[3] == 't')
