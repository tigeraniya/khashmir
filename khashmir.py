## Copyright 2002 Andrew Loewenstern, All Rights Reserved

from const import reactor
import time

from ktable import KTable, K
from knode import KNode as Node

from hash import newID

from actions import FindNode, GetValue
from twisted.web import xmlrpc
from twisted.internet.defer import Deferred
from twisted.python import threadable
from twisted.internet.app import Application
from twisted.web import server
threadable.init()

from bsddb3 import db ## find this at http://pybsddb.sf.net/
from bsddb3._db import DBNotFoundError

# don't ping unless it's been at least this many seconds since we've heard from a peer
MAX_PING_INTERVAL = 60 * 15 # fifteen minutes



# this is the main class!
class Khashmir(xmlrpc.XMLRPC):
    __slots__ = ['listener', 'node', 'table', 'store', 'app']
    def __init__(self, host, port):
	self.node = Node(newID(), host, port)
	self.table = KTable(self.node)
	self.app = Application("xmlrpc")
	self.app.listenTCP(port, server.Site(self))
	self.store = db.DB()
	self.store.open(None, None, db.DB_BTREE)
	

    def render(self, request):
	"""
	    Override the built in render so we can have access to the request object!
	    note, crequest is probably only valid on the initial call (not after deferred!)
	"""
	self.crequest = request
	return xmlrpc.XMLRPC.render(self, request)

	
    #######
    #######  LOCAL INTERFACE    - use these methods!
    def addContact(self, host, port):
	"""
	 ping this node and add the contact info to the table on pong!
	"""
	n =Node(" "*20, host, port)  # note, we 
	self.sendPing(n)


    ## this call is async!
    def findNode(self, id, callback, errback=None):
	""" returns the contact info for node, or the k closest nodes, from the global table """
	# get K nodes out of local table/cache, or the node we want
	nodes = self.table.findNodes(id)
	d = Deferred()
	d.addCallbacks(callback, errback)
	if len(nodes) == 1 and nodes[0].id == id :
	    d.callback(nodes)
	else:
	    # create our search state
	    state = FindNode(self, id, d.callback)
	    reactor.callFromThread(state.goWithNodes, nodes)
    
    
    ## also async
    def valueForKey(self, key, callback):
	""" returns the values found for key in global table """
	nodes = self.table.findNodes(key)
	# create our search state
	state = GetValue(self, key, callback)
	reactor.callFromThread(state.goWithNodes, nodes)


    ## async, but in the current implementation there is no guarantee a store does anything so there is no callback right now
    def storeValueForKey(self, key, value):
	""" stores the value for key in the global table, returns immediately, no status 
	    in this implementation, peers respond but don't indicate status to storing values
	    values are stored in peers on a first-come first-served basis
	    this will probably change so more than one value can be stored under a key
	"""
	def _storeValueForKey(nodes, key=key, value=value, response= self._storedValueHandler, default= lambda t: "didn't respond"):
	    for node in nodes:
		if node.id != self.node.id:
		    df = node.storeValue(key, value, self.node.senderDict())
		    df.addCallbacks(response, default)
	# this call is asynch
	self.findNode(key, _storeValueForKey)
	
	
    def insertNode(self, n):
	"""
	insert a node in our local table, pinging oldest contact in bucket, if necessary
	
	If all you have is a host/port, then use addContact, which calls this method after
	receiving the PONG from the remote node.  The reason for the seperation is we can't insert
	a node into the table without it's peer-ID.  That means of course the node passed into this
	method needs to be a properly formed Node object with a valid ID.
	"""
	old = self.table.insertNode(n)
	if old and (time.time() - old.lastSeen) > MAX_PING_INTERVAL and old.id != self.node.id:
	    # the bucket is full, check to see if old node is still around and if so, replace it
	    
	    ## these are the callbacks used when we ping the oldest node in a bucket
	    def _staleNodeHandler(oldnode=old, newnode = n):
		""" called if the pinged node never responds """
		self.table.replaceStaleNode(old, newnode)
	
	    def _notStaleNodeHandler(sender, old=old):
		""" called when we get a ping from the remote node """
		if sender['id'] == old.id:
		    self.table.insertNode(old)

	    df = old.ping()
	    df.addCallbacks(_notStaleNodeHandler, self._staleNodeHandler)


    def sendPing(self, node):
	"""
	    ping a node
	"""
	df = node.ping(self.node.senderDict())
	## these are the callbacks we use when we issue a PING
	def _pongHandler(sender, id=node.id, host=node.host, port=node.port, table=self.table):
	    if id != 20 * ' ' and id != sender['id']:
		# whoah, got response from different peer than we were expecting
		pass
	    else:
		#print "Got PONG from %s at %s:%s" % (`msg['id']`, t.target.host, t.target.port)
		n = Node(sender['id'], host, port)
		table.insertNode(n)
	    return
	def _defaultPong(err):
	    # this should probably increment a failed message counter and dump the node if it gets over a threshold
	    return	

	df.addCallbacks(_pongHandler,_defaultPong)


    def findCloseNodes(self):
	"""
	    This does a findNode on the ID one away from our own.  
	    This will allow us to populate our table with nodes on our network closest to our own.
	    This is called as soon as we start up with an empty table
	"""
	id = self.node.id[:-1] + chr((ord(self.node.id[-1]) + 1) % 256)
	def callback(nodes):
	    pass
	self.findNode(id, callback)

    def refreshTable(self):
	"""
	    
	"""
	def callback(nodes):
	    pass

	for bucket in self.table.buckets:
	    if time.time() - bucket.lastAccessed >= 60 * 60:
		id = randRange(bucket.min, bucket.max)
		self.findNode(id, callback)
	
 
    #####
    ##### INCOMING MESSAGE HANDLERS
    
    def xmlrpc_ping(self, sender):
	"""
	    takes sender dict = {'id', <id>, 'port', port} optional keys = 'ip'
	    returns sender dict
	"""
	ip = self.crequest.getClientIP()
	n = Node(sender['id'], ip, sender['port'])
	self.insertNode(n)
	return self.node.senderDict()
		
    def xmlrpc_find_node(self, target, sender):
	nodes = self.table.findNodes(target)
	nodes = map(lambda node: node.senderDict(), nodes)
	ip = self.crequest.getClientIP()
	n = Node(sender['id'], ip, sender['port'])
	self.insertNode(n)
	return nodes, self.node.senderDict()
    
    def xmlrpc_store_value(self, key, value, sender):
	if not self.store.has_key(key):
	    self.store.put(key, value)
	ip = self.crequest.getClientIP()
	n = Node(sender['id'], ip, sender['port'])
	self.insertNode(n)
	return self.node.senderDict()
	
    def xmlrpc_find_value(self, key, sender):
    	ip = self.crequest.getClientIP()
	n = Node(sender['id'], ip, sender['port'])
	self.insertNode(n)
	if self.store.has_key(key):
	    return {'values' : self.store[key]}, self.node.senderDict()
	else:
	    nodes = self.table.findNodes(key)
	    nodes = map(lambda node: node.senderDict(), nodes)
	    return {'nodes' : nodes}, self.node.senderDict()

    ###
    ### message response callbacks
    # called when we get a response to store value
    def _storedValueHandler(self, sender):
	pass






#------ testing

def test_build_net(quiet=0, peers=256, pause=1):
    from whrandom import randrange
    import thread
    port = 2001
    l = []
        
    if not quiet:
	print "Building %s peer table." % peers
	
    for i in xrange(peers):
	a = Khashmir('localhost', port + i)
	l.append(a)
    

    thread.start_new_thread(l[0].app.run, ())
    time.sleep(1)
    for peer in l[1:]:
	peer.app.run()
	#time.sleep(.25)

    print "adding contacts...."

    for peer in l[1:]:
	n = l[randrange(0, len(l))].node
	peer.addContact(n.host, n.port)
	n = l[randrange(0, len(l))].node
	peer.addContact(n.host, n.port)
	n = l[randrange(0, len(l))].node
	peer.addContact(n.host, n.port)
	if pause:
	    time.sleep(.30)
	    
    time.sleep(5)
    print "finding close nodes...."

    for peer in l:
	peer.findCloseNodes()
	if pause:
	    time.sleep(1)
#    for peer in l:
#	peer.refreshTable()
    return l
        
def test_find_nodes(l, quiet=0):
    import threading, sys
    from whrandom import randrange
    flag = threading.Event()
    
    n = len(l)
    
    a = l[randrange(0,n)]
    b = l[randrange(0,n)]
    
    def callback(nodes, l=l, flag=flag):
	if (len(nodes) >0) and (nodes[0].id == b.node.id):
	    print "test_find_nodes	PASSED"
	else:
	    print "test_find_nodes	FAILED"
	flag.set()
    a.findNode(b.node.id, callback)
    flag.wait()
    
def test_find_value(l, quiet=0):
    from whrandom import randrange
    from sha import sha
    import time, threading, sys
    
    fa = threading.Event()
    fb = threading.Event()
    fc = threading.Event()
    
    n = len(l)
    a = l[randrange(0,n)]
    b = l[randrange(0,n)]
    c = l[randrange(0,n)]
    d = l[randrange(0,n)]

    key = sha(`randrange(0,100000)`).digest()
    value = sha(`randrange(0,100000)`).digest()
    if not quiet:
	print "inserting value...",
	sys.stdout.flush()
    a.storeValueForKey(key, value)
    time.sleep(3)
    print "finding..."
    
    def mc(flag, value=value):
	def callback(values, f=flag, val=value):
	    try:
		if(len(values) == 0):
		    print "find                FAILED"
		else:
		    if values != val:
			print "find                FAILED"
		    else:
			print "find                FOUND"
	    finally:
		f.set()
	return callback
    b.valueForKey(key, mc(fa))
    fa.wait()
    c.valueForKey(key, mc(fb))
    fb.wait()
    d.valueForKey(key, mc(fc))    
    fc.wait()
    
if __name__ == "__main__":
    l = test_build_net()
    time.sleep(3)
    print "finding nodes..."
    test_find_nodes(l)
    test_find_nodes(l)
    test_find_nodes(l)
    print "inserting and fetching values..."
    test_find_value(l)
    test_find_value(l)
    test_find_value(l)
    test_find_value(l)
    test_find_value(l)
    test_find_value(l)
