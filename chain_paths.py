#!/usr/bin/env python
#
# Inkscape extension making long continuous paths from shorter pieces.
# (C) 2015 juewei@fabfolk.com
#
# code snippets visited to learn the extension 'effect' interface:
# - http://github.com/jnweiger/inkscape-silhouette
# - http://github.com/jnweiger/inkscape-gears-dev
# - http://sourceforge.net/projects/inkcut/
# - http://code.google.com/p/inkscape2tikz/
# - http://wiki.inkscape.org/wiki/index.php/PythonEffectTutorial
# - http://code.google.com/p/eggbotcode/
# - http://www.bobcookdev.com/inkscape/better_dxf_output.zip
#
# 2015-11-15 jw, V0.1 -- initial draught

__version__ = '0.1'	# Keep in sync with sendto_silhouette.inx ca line 42
__author__ = 'Juergen Weigert <juewei@fabfolk.com>'

import sys, os, shutil, time, logging, tempfile


sys_platform = sys.platform.lower()
if sys_platform.startswith('win'):
  sys.path.append('C:\Program Files\Inkscape\share\extensions')

elif sys_platform.startswith('darwin'):
  sys.path.append('/Applications/Inkscape.app/Contents/Resources/extensions')

else:   # linux
  # if sys_platform.startswith('linux'):
  sys.path.append('/usr/share/inkscape/extensions')

# We will use the inkex module with the predefined Effect base class.
import inkex
from bezmisc import *
from simpletransform import *
import simplepath
import cubicsuperpath
import cspsubdiv
import string   # for string.lstrip
import gettext
from optparse import SUPPRESS_HELP

def px2mm(px):
  '''
  Convert inkscape pixels to mm.
  The default inkscape unit, called 'px' is 90dpi
  '''
  return px*25.4/90

# Lifted with impunity from eggbot.py
def parseLengthWithUnits( str ):

        '''
        Parse an SVG value which may or may not have units attached
        This version is greatly simplified in that it only allows: no units,
        units of px, mm, and %.  Everything else, it returns None for.
        There is a more general routine to consider in scour.py if more
        generality is ever needed.
        '''

        u = 'px'
        s = str.strip()
        if s[-2:] == 'px':
                s = s[:-2]
        elif s[-2:] == 'mm':
                u = 'mm'
                s = s[:-2]
        elif s[-1:] == '%':
                u = '%'
                s = s[:-1]
        try:
                v = float( s )
        except:
                return None, None

        return v, u


def subdivideCubicPath( sp, flat, i=1 ):

        """
        Break up a bezier curve into smaller curves, each of which
        is approximately a straight line within a given tolerance
        (the "smoothness" defined by [flat]).

        This is a modified version of cspsubdiv.cspsubdiv() rewritten
        to avoid recurrence.
        """

        while True:
                while True:
                        if i >= len( sp ):
                                return

                        p0 = sp[i - 1][1]
                        p1 = sp[i - 1][2]
                        p2 = sp[i][0]
                        p3 = sp[i][1]

                        b = ( p0, p1, p2, p3 )

                        if cspsubdiv.maxdist( b ) > flat:
                                break

                        i += 1

                one, two = beziersplitatt( b, 0.5 )
                sp[i - 1][2] = one[1]
                sp[i][0] = two[2]
                p = [one[2], one[3], two[1]]
                sp[i:1] = [p]



class ChainPaths(inkex.Effect):
  """
  Inkscape Extension make long continuous paths from smaller parts
  """
  def __init__(self):
    # Call the base class constructor.
    inkex.Effect.__init__(self)

    # For handling an SVG viewbox attribute, we will need to know the
    # values of the document's <svg> width and height attributes as well
    # as establishing a transform from the viewbox to the display.
    self.chain_epsilon = float( N_CHAIN_EPS )

    self.dumpname= os.path.join(tempfile.gettempdir(), "chain_paths.dump")

    try:
      self.tty = open("/dev/tty", 'w')
    except:
      self.tty = open(os.devnull, 'w')  # '/dev/null' for POSIX, 'nul' for Windows.
    # print >>self.tty, "__init__"

    self.OptionParser.add_option('-V', '--version',
          action = 'store_const', const=True, dest = 'version', default = False,
          help='Just print version number ("'+__version__+'") and exit.')
    self.OptionParser.add_option('--snap', action = 'store', dest = 'snap', type = 'inkbool', default = False, help='snap end-points together if they are within epsilon')
    self.OptionParser.add_option('--epsilon', action = 'store',
          type = 'float', dest = 'chain_epsilon', default = 0.01, help="Max. identical point distance [mm]")

  def version(self):
    return __version__
  def author(self):
    return __author__


  ## lifted from eggbot.py, gratefully bowing to the author
  def plotPath( self, path, matTransform ):
                '''
                Plot the path while applying the transformation defined
                by the matrix [matTransform].
                '''
                # turn this path into a cubicsuperpath (list of beziers)...

                d = path.get( 'd' )

                if len( simplepath.parsePath( d ) ) == 0:
                        return

                p = cubicsuperpath.parsePath( d )

                # ...and apply the transformation to each point
                applyTransformToPath( matTransform, p )

                # p is now a list of lists of cubic beziers [control pt1, control pt2, endpoint]
                # where the start-point is the last point in the previous segment.
                for sp in p:

                        subdivideCubicPath( sp, self.options.smoothness )
                        nIndex = 0

                        for csp in sp:

                                if self.bStopped:
                                        return

                                if self.plotCurrentLayer:
                                        if nIndex == 0:
                                                self.penUp()
                                                self.virtualPenIsUp = True
                                        elif nIndex == 1:
                                                self.penDown()
                                                self.virtualPenIsUp = False

                                nIndex += 1

                                self.fX = float( csp[1][0] ) / self.step_scaling_factor
                                self.fY = float( csp[1][1] ) / self.step_scaling_factor

                                # store home
                                if self.ptFirst is None:
                                        self.ptFirst = ( self.fX, self.fY )

                                if self.plotCurrentLayer:
                                        self.plotLineAndTime()
                                        self.fPrevX = self.fX
                                        self.fPrevY = self.fY


  def DoWePlotLayer( self, strLayerName ):
                """
                We are only plotting *some* layers. Check to see
                whether or not we're going to plot this one.

                First: scan first 4 chars of node id for first non-numeric character,
                and scan the part before that (if any) into a number

                Then, see if the number matches the layer.
                """

                TempNumString = 'x'
                stringPos = 1
                CurrentLayerName = string.lstrip( strLayerName ) #remove leading whitespace

                # Look at layer name.  Sample first character, then first two, and
                # so on, until the string ends or the string no longer consists of
                # digit characters only.

                MaxLength = len( CurrentLayerName )
                if MaxLength > 0:
                        while stringPos <= MaxLength:
                                if str.isdigit( CurrentLayerName[:stringPos] ):
                                        TempNumString = CurrentLayerName[:stringPos] # Store longest numeric string so far
                                        stringPos = stringPos + 1
                                else:
                                        break

                self.plotCurrentLayer = False    #Temporarily assume that we aren't plotting the layer
                if ( str.isdigit( TempNumString ) ):
                        if ( self.svgLayer == int( float( TempNumString ) ) ):
                                self.plotCurrentLayer = True    #We get to plot the layer!
                                self.LayersPlotted += 1
                #Note: this function is only called if we are NOT plotting all layers.


  def recursivelyTraverseSvg( self, aNodeList,
                        matCurrent=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                        parent_visibility='visible' ):
                """
                Recursively traverse the svg file to plot out all of the
                paths.  The function keeps track of the composite transformation
                that should be applied to each path.

                This function handles path, group, line, rect, polyline, polygon,
                circle, ellipse and use (clone) elements.  Notable elements not
                handled include text.  Unhandled elements should be converted to
                paths in Inkscape.
                """
                if not self.plotCurrentLayer:
                  return        # saves us a lot of time ...

                for node in aNodeList:
                        # Ignore invisible nodes
                        v = node.get( 'visibility', parent_visibility )
                        if v == 'inherit':
                                v = parent_visibility
                        if v == 'hidden' or v == 'collapse':
                                pass

                        # first apply the current matrix transform to this node's tranform
                        matNew = composeTransform( matCurrent, parseTransform( node.get( "transform" ) ) )

                        if node.tag == inkex.addNS( 'g', 'svg' ) or node.tag == 'g':

                                self.penUp()
                                if ( node.get( inkex.addNS( 'groupmode', 'inkscape' ) ) == 'layer' ):
                                        if (node.get('style','') == 'display:none'):
                                                self.plotCurrentLayer = False
                                        else:
                                                self.plotCurrentLayer = True

                                        if not self.allLayers:
                                                # inkex.errormsg('Plotting layer named: ' + node.get(inkex.addNS('label', 'inkscape')))
                                                self.DoWePlotLayer( node.get( inkex.addNS( 'label', 'inkscape' ) ) )
                                self.recursivelyTraverseSvg( node, matNew, parent_visibility=v )

                        elif node.tag == inkex.addNS( 'use', 'svg' ) or node.tag == 'use':

                                # A <use> element refers to another SVG element via an xlink:href="#blah"
                                # attribute.  We will handle the element by doing an XPath search through
                                # the document, looking for the element with the matching id="blah"
                                # attribute.  We then recursively process that element after applying
                                # any necessary (x,y) translation.
                                #
                                # Notes:
                                #  1. We ignore the height and width attributes as they do not apply to
                                #     path-like elements, and
                                #  2. Even if the use element has visibility="hidden", SVG still calls
                                #     for processing the referenced element.  The referenced element is
                                #     hidden only if its visibility is "inherit" or "hidden".

                                refid = node.get( inkex.addNS( 'href', 'xlink' ) )
                                if refid:
                                        # [1:] to ignore leading '#' in reference
                                        path = '//*[@id="%s"]' % refid[1:]
                                        refnode = node.xpath( path )
                                        if refnode:
                                                x = float( node.get( 'x', '0' ) )
                                                y = float( node.get( 'y', '0' ) )
                                                # Note: the transform has already been applied
                                                if ( x != 0 ) or (y != 0 ):
                                                        matNew2 = composeTransform( matNew, parseTransform( 'translate(%f,%f)' % (x,y) ) )
                                                else:
                                                        matNew2 = matNew
                                                v = node.get( 'visibility', v )
                                                self.recursivelyTraverseSvg( refnode, matNew2, parent_visibility=v )
                                        else:
                                                pass
                                else:
                                        pass

                        elif node.tag == inkex.addNS( 'path', 'svg' ):

                                self.pathcount += 1

                                # if we're in resume mode AND self.pathcount < self.svgLastPath,
                                #    then skip over this path.
                                # if we're in resume mode and self.pathcount = self.svgLastPath,
                                #    then start here, and set
                                # self.nodeCount equal to self.svgLastPathNC
                                if self.resumeMode and ( self.pathcount == self.svgLastPath ):
                                        self.nodeCount = self.svgLastPathNC
                                if self.resumeMode and ( self.pathcount < self.svgLastPath ):
                                        pass
                                else:
                                        self.plotPath( node, matNew )
                                        if ( not self.bStopped ):       #an "index" for resuming plots quickly-- record last complete path
                                                self.svgLastPath += 1
                                                self.svgLastPathNC = self.nodeCount

                        elif node.tag == inkex.addNS( 'rect', 'svg' ) or node.tag == 'rect':

                                # Manually transform
                                #
                                #    <rect x="X" y="Y" width="W" height="H"/>
                                #
                                # into
                                #
                                #    <path d="MX,Y lW,0 l0,H l-W,0 z"/>
                                #
                                # I.e., explicitly draw three sides of the rectangle and the
                                # fourth side implicitly

                                self.pathcount += 1
                                # if we're in resume mode AND self.pathcount < self.svgLastPath,
                                #    then skip over this path.
                                # if we're in resume mode and self.pathcount = self.svgLastPath,
                                #    then start here, and set
                                # self.nodeCount equal to self.svgLastPathNC
                                if self.resumeMode and ( self.pathcount == self.svgLastPath ):
                                        self.nodeCount = self.svgLastPathNC
                                if self.resumeMode and ( self.pathcount < self.svgLastPath ):
                                        pass
                                else:
                                        # Create a path with the outline of the rectangle
                                        newpath = inkex.etree.Element( inkex.addNS( 'path', 'svg' ) )
                                        x = float( node.get( 'x' ) )
                                        y = float( node.get( 'y' ) )
                                        w = float( node.get( 'width' ) )
                                        h = float( node.get( 'height' ) )
                                        s = node.get( 'style' )
                                        if s:
                                                newpath.set( 'style', s )
                                        t = node.get( 'transform' )
                                        if t:
                                                newpath.set( 'transform', t )
                                        a = []
                                        a.append( ['M ', [x, y]] )
                                        a.append( [' l ', [w, 0]] )
                                        a.append( [' l ', [0, h]] )
                                        a.append( [' l ', [-w, 0]] )
                                        a.append( [' Z', []] )
                                        newpath.set( 'd', simplepath.formatPath( a ) )
                                        self.plotPath( newpath, matNew )

                        elif node.tag == inkex.addNS( 'line', 'svg' ) or node.tag == 'line':

                                # Convert
                                #
                                #   <line x1="X1" y1="Y1" x2="X2" y2="Y2/>
                                #
                                # to
                                #
                                #   <path d="MX1,Y1 LX2,Y2"/>

                                self.pathcount += 1
                                # if we're in resume mode AND self.pathcount < self.svgLastPath,
                                #    then skip over this path.
                                # if we're in resume mode and self.pathcount = self.svgLastPath,
                                #    then start here, and set
                                # self.nodeCount equal to self.svgLastPathNC

                                if self.resumeMode and ( self.pathcount == self.svgLastPath ):
                                        self.nodeCount = self.svgLastPathNC
                                if self.resumeMode and ( self.pathcount < self.svgLastPath ):
                                        pass
                                else:
                                        # Create a path to contain the line
                                        newpath = inkex.etree.Element( inkex.addNS( 'path', 'svg' ) )
                                        x1 = float( node.get( 'x1' ) )
                                        y1 = float( node.get( 'y1' ) )
                                        x2 = float( node.get( 'x2' ) )
                                        y2 = float( node.get( 'y2' ) )
                                        s = node.get( 'style' )
                                        if s:
                                                newpath.set( 'style', s )
                                        t = node.get( 'transform' )
                                        if t:
                                                newpath.set( 'transform', t )
                                        a = []
                                        a.append( ['M ', [x1, y1]] )
                                        a.append( [' L ', [x2, y2]] )
                                        newpath.set( 'd', simplepath.formatPath( a ) )
                                        self.plotPath( newpath, matNew )
                                        if ( not self.bStopped ):       #an "index" for resuming plots quickly-- record last complete path
                                                self.svgLastPath += 1
                                                self.svgLastPathNC = self.nodeCount

                        elif node.tag == inkex.addNS( 'polyline', 'svg' ) or node.tag == 'polyline':

                                # Convert
                                #
                                #  <polyline points="x1,y1 x2,y2 x3,y3 [...]"/>
                                #
                                # to
                                #
                                #   <path d="Mx1,y1 Lx2,y2 Lx3,y3 [...]"/>
                                #
                                # Note: we ignore polylines with no points

                                pl = node.get( 'points', '' ).strip()
                                if pl == '':
                                        pass

                                self.pathcount += 1
                                #if we're in resume mode AND self.pathcount < self.svgLastPath, then skip over this path.
                                #if we're in resume mode and self.pathcount = self.svgLastPath, then start here, and set
                                # self.nodeCount equal to self.svgLastPathNC

                                if self.resumeMode and ( self.pathcount == self.svgLastPath ):
                                        self.nodeCount = self.svgLastPathNC

                                if self.resumeMode and ( self.pathcount < self.svgLastPath ):
                                        pass

                                else:
                                        pa = pl.split()
                                        if not len( pa ):
                                                pass
                                        # Issue 29: pre 2.5.? versions of Python do not have
                                        #    "statement-1 if expression-1 else statement-2"
                                        # which came out of PEP 308, Conditional Expressions
                                        #d = "".join( ["M " + pa[i] if i == 0 else " L " + pa[i] for i in range( 0, len( pa ) )] )
                                        d = "M " + pa[0]
                                        for i in range( 1, len( pa ) ):
                                                d += " L " + pa[i]
                                        newpath = inkex.etree.Element( inkex.addNS( 'path', 'svg' ) )
                                        newpath.set( 'd', d );
                                        s = node.get( 'style' )
                                        if s:
                                                newpath.set( 'style', s )
                                        t = node.get( 'transform' )
                                        if t:
                                                newpath.set( 'transform', t )
                                        self.plotPath( newpath, matNew )
                                        if ( not self.bStopped ):       #an "index" for resuming plots quickly-- record last complete path
                                                self.svgLastPath += 1
                                                self.svgLastPathNC = self.nodeCount

                        elif node.tag == inkex.addNS( 'polygon', 'svg' ) or node.tag == 'polygon':

                                # Convert
                                #
                                #  <polygon points="x1,y1 x2,y2 x3,y3 [...]"/>
                                #
                                # to
                                #
                                #   <path d="Mx1,y1 Lx2,y2 Lx3,y3 [...] Z"/>
                                #
                                # Note: we ignore polygons with no points

                                pl = node.get( 'points', '' ).strip()
                                if pl == '':
                                        pass

                                self.pathcount += 1
                                #if we're in resume mode AND self.pathcount < self.svgLastPath, then skip over this path.
                                #if we're in resume mode and self.pathcount = self.svgLastPath, then start here, and set
                                # self.nodeCount equal to self.svgLastPathNC

                                if self.resumeMode and ( self.pathcount == self.svgLastPath ):
                                        self.nodeCount = self.svgLastPathNC

                                if self.resumeMode and ( self.pathcount < self.svgLastPath ):
                                        pass

                                else:
                                        pa = pl.split()
                                        if not len( pa ):
                                                pass
                                        # Issue 29: pre 2.5.? versions of Python do not have
                                        #    "statement-1 if expression-1 else statement-2"
                                        # which came out of PEP 308, Conditional Expressions
                                        #d = "".join( ["M " + pa[i] if i == 0 else " L " + pa[i] for i in range( 0, len( pa ) )] )
                                        d = "M " + pa[0]
                                        for i in range( 1, len( pa ) ):
                                                d += " L " + pa[i]
                                        d += " Z"
                                        newpath = inkex.etree.Element( inkex.addNS( 'path', 'svg' ) )
                                        newpath.set( 'd', d );
                                        s = node.get( 'style' )
                                        if s:
                                                newpath.set( 'style', s )
                                        t = node.get( 'transform' )
                                        if t:
                                                newpath.set( 'transform', t )
                                        self.plotPath( newpath, matNew )
                                        if ( not self.bStopped ):       #an "index" for resuming plots quickly-- record last complete path
                                                self.svgLastPath += 1
                                                self.svgLastPathNC = self.nodeCount

                        elif node.tag == inkex.addNS( 'ellipse', 'svg' ) or \
                                node.tag == 'ellipse' or \
                                node.tag == inkex.addNS( 'circle', 'svg' ) or \
                                node.tag == 'circle':

                                        # Convert circles and ellipses to a path with two 180 degree arcs.
                                        # In general (an ellipse), we convert
                                        #
                                        #   <ellipse rx="RX" ry="RY" cx="X" cy="Y"/>
                                        #
                                        # to
                                        #
                                        #   <path d="MX1,CY A RX,RY 0 1 0 X2,CY A RX,RY 0 1 0 X1,CY"/>
                                        #
                                        # where
                                        #
                                        #   X1 = CX - RX
                                        #   X2 = CX + RX
                                        #
                                        # Note: ellipses or circles with a radius attribute of value 0 are ignored

                                        if node.tag == inkex.addNS( 'ellipse', 'svg' ) or node.tag == 'ellipse':
                                                rx = float( node.get( 'rx', '0' ) )
                                                ry = float( node.get( 'ry', '0' ) )
                                        else:
                                                rx = float( node.get( 'r', '0' ) )
                                                ry = rx
                                        if rx == 0 or ry == 0:
                                                pass

                                        self.pathcount += 1
                                        #if we're in resume mode AND self.pathcount < self.svgLastPath, then skip over this path.
                                        #if we're in resume mode and self.pathcount = self.svgLastPath, then start here, and set
                                        # self.nodeCount equal to self.svgLastPathNC

                                        if self.resumeMode and ( self.pathcount == self.svgLastPath ):
                                                self.nodeCount = self.svgLastPathNC

                                        if self.resumeMode and ( self.pathcount < self.svgLastPath ):
                                                pass

                                        else:
                                                cx = float( node.get( 'cx', '0' ) )
                                                cy = float( node.get( 'cy', '0' ) )
                                                x1 = cx - rx
                                                x2 = cx + rx
                                                d = 'M %f,%f ' % ( x1, cy ) + \
                                                        'A %f,%f ' % ( rx, ry ) + \
                                                        '0 1 0 %f,%f ' % ( x2, cy ) + \
                                                        'A %f,%f ' % ( rx, ry ) + \
                                                        '0 1 0 %f,%f' % ( x1, cy )
                                                newpath = inkex.etree.Element( inkex.addNS( 'path', 'svg' ) )
                                                newpath.set( 'd', d );
                                                s = node.get( 'style' )
                                                if s:
                                                        newpath.set( 'style', s )
                                                t = node.get( 'transform' )
                                                if t:
                                                        newpath.set( 'transform', t )
                                                self.plotPath( newpath, matNew )
                                                if ( not self.bStopped ):       #an "index" for resuming plots quickly-- record last complete path
                                                        self.svgLastPath += 1
                                                        self.svgLastPathNC = self.nodeCount
                        elif node.tag == inkex.addNS( 'metadata', 'svg' ) or node.tag == 'metadata':
                                pass
                        elif node.tag == inkex.addNS( 'defs', 'svg' ) or node.tag == 'defs':
                                pass
                        elif node.tag == inkex.addNS( 'namedview', 'sodipodi' ) or node.tag == 'namedview':
                                pass
                        elif node.tag == inkex.addNS( 'eggbot', 'svg' ) or node.tag == 'eggbot':
                                pass
                        elif node.tag == inkex.addNS( 'title', 'svg' ) or node.tag == 'title':
                                pass
                        elif node.tag == inkex.addNS( 'desc', 'svg' ) or node.tag == 'desc':
                                pass
                        elif node.tag == inkex.addNS( 'text', 'svg' ) or node.tag == 'text':
                                texts = []
                                plaintext = ''
                                if self.plotCurrentLayer:
                                  for tnode in node.iterfind('.//'): # all subtree
                                    if tnode is not None and tnode.text is not None:
                                      texts.append(tnode.text)
                                if len(texts):
                                  plaintext = "', '".join(texts).encode('latin-1')
                                  # encode_('latin-1') prevents 'ordinal not in range(128)' errors.
                                  print >>self.tty, "Text ignored: '%s'" % (plaintext)
                                  plaintext = "\n".join(texts)+"\n"

                                  if not self.warnings.has_key( 'text' ) and self.plotCurrentLayer:
                                        inkex.errormsg( plaintext + gettext.gettext( 'Warning: unable to draw text; ' +
                                                'please convert it to a path first. Or consider using the ' +
                                                'Hershey Text extension which can be installed in the '+
                                                '"Render" category of extensions.' ) )
                                        self.warnings['text'] = 1
                                pass
                        elif node.tag == inkex.addNS( 'image', 'svg' ) or node.tag == 'image':
                                if not self.warnings.has_key( 'image' ):
                                        inkex.errormsg( gettext.gettext( 'Warning: unable to draw bitmap images; ' +
                                                'please convert them to line art first.  Consider using the "Trace bitmap..." ' +
                                                'tool of the "Path" menu.  Mac users please note that some X11 settings may ' +
                                                'cause cut-and-paste operations to paste in bitmap copies.' ) )
                                        self.warnings['image'] = 1
                                pass
                        elif node.tag == inkex.addNS( 'pattern', 'svg' ) or node.tag == 'pattern':
                                pass
                        elif node.tag == inkex.addNS( 'radialGradient', 'svg' ) or node.tag == 'radialGradient':
                                # Similar to pattern
                                pass
                        elif node.tag == inkex.addNS( 'linearGradient', 'svg' ) or node.tag == 'linearGradient':
                                # Similar in pattern
                                pass
                        elif node.tag == inkex.addNS( 'style', 'svg' ) or node.tag == 'style':
                                # This is a reference to an external style sheet and not the value
                                # of a style attribute to be inherited by child elements
                                pass
                        elif node.tag == inkex.addNS( 'cursor', 'svg' ) or node.tag == 'cursor':
                                pass
                        elif node.tag == inkex.addNS( 'flowRoot', 'svg' ) or node.tag == 'flowRoot':
                                # contains a <flowRegion><rect y="91" x="369" height="383" width="375" ...
                                # see examples/fablab_logo_stencil.svg
                                pass
                        elif node.tag == inkex.addNS( 'color-profile', 'svg' ) or node.tag == 'color-profile':
                                # Gamma curves, color temp, etc. are not relevant to single color output
                                pass
                        elif not isinstance( node.tag, basestring ):
                                # This is likely an XML processing instruction such as an XML
                                # comment.  lxml uses a function reference for such node tags
                                # and as such the node tag is likely not a printable string.
                                # Further, converting it to a printable string likely won't
                                # be very useful.
                                pass
                        else:
                                if not self.warnings.has_key( str( node.tag ) ):
                                        t = str( node.tag ).split( '}' )
                                        inkex.errormsg( gettext.gettext( 'Warning: unable to draw <' + str( t[-1] ) +
                                                '> object, please convert it to a path first.' ) )
                                        self.warnings[str( node.tag )] = 1
                                pass


  def getLength( self, name, default ):

                '''
                Get the <svg> attribute with name "name" and default value "default"
                Parse the attribute into a value and associated units.  Then, accept
                no units (''), units of pixels ('px'), and units of percentage ('%').
                '''

                str = self.document.getroot().get( name )
                # print >>self.tty, "getLength.str", str
                if str:
                        v, u = parseLengthWithUnits( str )
                        # print >>self.tty, "parseLengthWithUnits: ", str, u, v
                        if not v:
                                # Couldn't parse the value
                                return None
                        elif ( u == '' ) or ( u == 'px' ):
                                return v
                        elif u == 'mm':
                                return v*90./25.4       # inverse of px2mm
                        elif u == '%':
                                return float( default ) * v / 100.0
                        else:
                                print >>sys.stderr, "unknown unit ", u
                                return None
                else:
                        # No width specified; assume the default value
                        return float( default )


  def getDocProps( self ):

                '''
                Get the document's height and width attributes from the <svg> tag.
                Use a default value in case the property is not present or is
                expressed in units of percentages.
                '''

                self.docHeight = self.getLength( 'height', N_PAGE_HEIGHT )
                print >>self.tty, "7 self.docHeight=", self.docHeight
                self.docWidth = self.getLength( 'width', N_PAGE_WIDTH )
                print >>self.tty, "8 self.docWidth=", self.docWidth
                if ( self.docHeight == None ) or ( self.docWidth == None ):
                        return False
                else:
                        return True


  def handleViewBox( self ):

                '''
                Set up the document-wide transform in the event that the document has an SVG viewbox
                '''

                if self.getDocProps():
                        viewbox = self.document.getroot().get( 'viewBox' )
                        if viewbox:
                                vinfo = viewbox.strip().replace( ',', ' ' ).split( ' ' )
                                if ( vinfo[2] != 0 ) and ( vinfo[3] != 0 ):
                                        sx = self.docWidth / float( vinfo[2] )
                                        sy = self.docHeight / float( vinfo[3] )
                                        self.docTransform = parseTransform( 'scale(%f,%f)' % (sx, sy) )


  def effect(self):
    if self.options.version:
      print __version__
      sys.exit(0)

    def write_progress(done, total, msg):
      if not 'write_start_tstamp' in self.__dict__:
        self.write_start_tstamp = time.time()
        self.device_buffer_perc = 0.0
      perc = 100.*done/total
      if time.time() - self.write_start_tstamp < 1.0:
        self.device_buffer_perc = perc
      buf = ""
      if self.device_buffer_perc > 1.0:
        buf = " (+%d%%)" % (self.device_buffer_perc+.5)
      self.tty.write("%d%%%s %s\r" % (perc-self.device_buffer_perc+.5, buf, msg))
      self.tty.flush()

    try:
      dev = SilhouetteCameo(log=self.tty, progress_cb=write_progress, no_device=self.options.dummy)
    except Exception as e:
      print >>self.tty, e
      print >>sys.stderr, e
      return
    state = dev.status()    # hint at loading paper, if not ready.
    print >>self.tty, "status=%s" % (state)
    print >>self.tty, "device version: '%s'" % dev.get_version()

    # Viewbox handling
    self.handleViewBox()
    # Build a list of the vertices for the document's graphical elements
    if self.options.ids:
      # Traverse the selected objects
      for id in self.options.ids:
        self.recursivelyTraverseSvg( [self.selected[id]], self.docTransform )
    else:
      # Traverse the entire document
      self.recursivelyTraverseSvg( self.document.getroot(), self.docTransform )

    self.pen=None
    if self.options.tool == 'pen': self.pen=True
    if self.options.tool == 'cut': self.pen=False

    if self.options.mat_free:
      mf = MatFree('default', scale=px2mm(1.0), pen=self.pen)
      mf.verbose = 0    # inkscape crashes whenever something appears in stdout.
      self.paths = mf.apply(self.paths)

    # print >>self.tty, self.paths
    cut = []
    pointcount = 0
    for px_path in self.paths:
      mm_path = []
      for pt in px_path:
        if self.options.mat_free:
          mm_path.append((pt[0], pt[1]))        # MatFree.load() did the scaling already.
        else:
          mm_path.append((px2mm(pt[0]), px2mm(pt[1])))
        pointcount += 1
      for i in range(0,self.options.multipass):
        if (self.options.reversetoggle):
          mm_path = list(reversed(mm_path))
        cut.append(mm_path)

    if dev.dev is None:
      docname=None
      svg = self.document.getroot()
      # Namespace horrors: Id's expand to full urls, before we can search them.
      # 'sodipodi:docname' -> '{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}docname'
      for tag in svg.attrib.keys():
        if re.search(r'}docname$',tag): docname=svg.get(tag)

      o = open(self.dumpname, 'w')
      print >>self.tty,   "Dump written to ",self.dumpname," (",pointcount," points )"
      print >>sys.stderr, "Dump written to ",self.dumpname," (",pointcount," points )"
      print >>sys.stderr,"device version: '%s'" % dev.get_version()
      print >>sys.stderr,"driver version: '%s'" % __version__
      print >>o,"# device version: '%s'" % dev.get_version()
      print >>o,"# driver version: '%s'" % __version__
      if docname:
        print >>o,"# docname: '%s'" % docname
        print >>sys.stderr, "docname: '%s'" % docname
      print >>o, cut

    if self.options.pressure == 0:     self.options.pressure = None
    if self.options.speed == 0:        self.options.speed = None
    dev.setup(media=int(self.options.media,10), pen=self.pen,
      pressure=self.options.pressure, speed=self.options.speed)

    if self.options.autocrop:
      # this takes much longer, if we have a complext drawing
      bbox = dev.plot(pathlist=cut,
        mediawidth=px2mm(self.docWidth),
        mediaheight=px2mm(self.docHeight),
        margintop=0, marginleft=0,
        bboxonly=None)         # only return the bbox, do not draw it.
      if len(bbox['bbox'].keys()):
        print >>self.tty, "autocrop left=%.1fmm top=%.1fmm" % (
          bbox['bbox']['llx']*bbox['unit'],
          bbox['bbox']['ury']*bbox['unit'])
        self.options.x_off -= bbox['bbox']['llx']*bbox['unit']
        self.options.y_off -= bbox['bbox']['ury']*bbox['unit']

    bbox = dev.plot(pathlist=cut,
      mediawidth=px2mm(self.docWidth),
      mediaheight=px2mm(self.docHeight),
      offset=(self.options.x_off,self.options.y_off),
      bboxonly=self.options.bboxonly)
    if len(bbox['bbox'].keys()) == 0:
      print >>self.tty, "empty page?"
      print >>sys.stderr, "empty page?"
    else:
      write_progress(1,1, "bbox: (%.1f,%.1f)-(%.1f,%.1f)mm, %d points" % (
        bbox['bbox']['llx']*bbox['unit'],
        bbox['bbox']['ury']*bbox['unit'],
        bbox['bbox']['urx']*bbox['unit'],
        bbox['bbox']['lly']*bbox['unit'],
        bbox['total']))
      print >>self.tty, ""
      state = dev.status()
      write_duration = time.time() - self.write_start_tstamp
      # we took write_duration seconds for actualy cutting
      # 100-device_buffer_perc percent of all data.
      # Thus we can compute the average write speed like this:
      if write_duration > 1.0:
        percent_per_sec = (100.0-self.device_buffer_perc) / write_duration
      else:
        percent_per_sec = 1000.     # unreliable data

      wait_sec = 1
      while (percent_per_sec*wait_sec < 1.6):   # max 60 dots
        wait_sec *= 2
      dots = '.'
      while self.options.wait_done and state == 'moving':
        time.sleep(wait_sec)
        self.device_buffer_perc -= wait_sec * percent_per_sec
        if self.device_buffer_perc < 0.0:
          self.device_buffer_perc = 0.0
        write_progress(1,1, dots)
        dots += '.'
        state = dev.status()
      self.device_buffer_perc = 0.0
      write_progress(1,1, dots)
    print >>self.tty, "\nstatus=%s" % (state)

    # pump the output to the device
    success = True
    if not success:
      logging.error('Failed to put output to device')
    output = ""
    return output

if __name__ == '__main__':
        e = ChainPaths()

        e.affect()
        sys.exit(0)    # helps to keep the selection
