import numpy as np
from enlib import enmap, utils, powspec, sharp, curvedsky, interpol

####### Flat sky lensing #######

def lens_map(imap, grad_phi, order=3, mode="spline", border="cyclic", trans=False):
	"""Lens map imap[{pre},ny,nx] according to grad_phi[2,ny,nx], where phi is the
	lensing potential, and grad_phi, which can be computed as enmap.grad(phi), simply
	is the coordinate displacement for each pixel. order, mode and border specify
	details of the interpolation used. See enlib.interpol.map_coordinates for details.
	If trans is true, the transpose operation is performed. This is NOT equivalent to
	delensing.

	If the same lensing field needs to be reused repeatedly, then higher efficiency
	can be gotten from calling displace_map directly with precomputed pixel positions."""
	pix = imap.sky2pix(imap.posmap() + grad_phi, safe=False)
	return displace_map(imap, pix, order=order, mode=mode, border=border, trans=trans)

def delens_map(imap, grad_phi, nstep=3, order=3, mode="spline", border="cyclic"):
	"""The inverse of lens_map, such that delens_map(lens_map(imap, dpos), dpos) = imap
	for well-behaved fields. The inverse does not always exist, in which case the
	equation above will only be approximately fulfilled. The inverse is computed by
	iteration, with the number of steps in the iteration controllable through the
	nstep parameter. See enlib.interpol.map_coordinates for details on the other
	parameters."""
	grad_phi = delens_grad(grad_phi, nstep=nstep, order=order, mode=mode, border=border)
	return lens_map(imap, -grad_phi, order=order, mode=mode, border=border)

def delens_grad(grad_phi, nstep=3, order=3, mode="spline", border="cyclic"):
	"""Helper function for delens_map. Attempts to find the undisplaced gradient
	given one that has been displaced by itself."""
	alpha = grad_phi
	for i in range(nstep):
		print i, alpha[:,0,0]
		alpha = lens_map(grad_phi, -alpha, order=order, mode=mode, border=border)
	return alpha

def displace_map(imap, pix, order=3, mode="spline", border="cyclic", trans=False):
	"""Displace map m[{pre},ny,nx] by pix[2,ny,nx], where pix indicates the location
	in the input map each output pixel should get its value from (float). The output
	is [{pre},ny,nx]."""
	iwork = np.empty(imap.shape[-2:]+imap.shape[:-2],imap.dtype)
	out   = imap.copy()
	# Why do we have to manually allocate outputs and juggle whcih is copied over here?
	# Because it is in general not possible to infer from odata what shape idata should have.
	# So the map_coordinates can't allocate the output array automatically in the transposed
	# case. But in this function we know that they will have the same shape, so we can.
	if not trans:
		iwork[:] = utils.moveaxes(imap, (-2,-1), (0,1))
		owork = interpol.map_coordinates(iwork, pix, order=order, mode=mode, border=border, trans=trans)
		out[:] = utils.moveaxes(owork, (0,1), (-2,-1))
	else:
		owork = iwork.copy()
		owork[:] = utils.moveaxes(imap, (-2,-1), (0,1))
		interpol.map_coordinates(iwork, pix, owork, order=order, mode=mode, border=border, trans=trans)
		out[:] = utils.moveaxes(iwork, (0,1), (-2,-1))
	return out

# Compatibility function. Not quite equivalent lens_map above due to taking phi rather than
# its gradient as an argument.
def lens_map_flat(cmb_map, phi_map):
	obs_pos  = cmb_map.posmap()
	grad_phi = enmap.ifft(enmap.map2harm(phi_map)*phi_map.lmap()*1j).real
	raw_pos  = obs_pos + grad_phi
	# Convert to pixel positions
	raw_pix  = cmb_map.sky2pix(raw_pos, safe=False)
	# And extract the interpolated values. Because of a bug in map_pixels with
	# mode="wrap", we must handle wrapping ourselves.
	npad = int(np.ceil(max(np.max(-raw_pix),np.max(raw_pix-np.array(cmb_map.shape[-2:])[:,None,None]))))
	pmap = enmap.pad(cmb_map, npad, wrap=True)
	return enmap.samewcs(utils.interpol(pmap, raw_pix+npad, order=4, mode="wrap"), cmb_map)


######## Curved sky lensing ########

def rand_map(shape, wcs, ps_cmb, ps_lens, lmax=None, dtype=np.float64, seed=None, oversample=2.0, spin=2, output="l", geodesic=True, verbose=False):
	ctype   = np.result_type(dtype,0j)
	ncomp   = ps_cmb.shape[0]
	# First draw a random lensing field, and use it to compute the undeflected positions
	if verbose: print "Computing observed coordinates"
	obs_pos = enmap.posmap(shape, wcs)
	if verbose: print "Generating phi alms"
	phi_alm = curvedsky.rand_alm(ps_lens, lmax=lmax, seed=seed, dtype=ctype)
	if "p" in output:
		if verbose: print "Computing phi map"
		phi_map = curvedsky.alm2map(phi_alm, obs_pos, oversample=oversample)
	if verbose: print "Computing grad map"
	# This gradient uses zenith coordinates, so the y-sign is flipped
	grad    = curvedsky.alm2map(phi_alm, obs_pos, oversample=oversample, deriv=True)
	grad[0] = -grad[0]
	if verbose: print "Computing alpha map"
	raw_pos = enmap.samewcs(offset_by_grad(obs_pos, grad, pol=ncomp>1, geodesic=geodesic), obs_pos)
	del phi_alm
	# Then draw a random CMB realization at the raw positions
	if verbose: print "Generating cmb alms"
	cmb_alm = curvedsky.rand_alm(ps_cmb, lmax=lmax, dtype=ctype) # already seeded
	if "u" in output:
		if verbose: print "Computing unlensed map"
		cmb_raw = curvedsky.alm2map(cmb_alm, obs_pos, oversample=oversample, spin=spin)
	if verbose: print "Computing lensed map"
	cmb_obs = curvedsky.alm2map(cmb_alm, raw_pos[:2], oversample=oversample, spin=spin)
	if raw_pos.shape[0] > 2 and np.any(raw_pos[2]):
		if verbose: print "Rotating polarization"
		cmb_obs = enmap.rotate_pol(cmb_obs, raw_pos[2])
	del cmb_alm
	# Output in same order as specified in output argument
	res = []
	for c in output:
		if c == "l": res.append(cmb_obs)
		elif c == "u": res.append(cmb_raw)
		elif c == "p": res.append(phi_map)
		elif c == "a": res.append(grad)
	return tuple(res)


def offset_by_grad(ipos, grad, geodesic=True, pol=None):
	"""Given a set of coordinates ipos[{dec,ra},...] and a gradient
	grad[{ddec,dphi/cos(dec)},...] (as returned by curvedsky.alm2map(deriv=True)),
	returns opos = ipos + grad, while properly parallel transporting
	on the sphere. If geodesic=False is specified, then an much faster
	approximation is used, which is still very accurate unless one is
	close to the poles."""
	ncomp = 2 if pol is False or pol is None and ipos.shape[0] <= 2 else 3
	opos = np.empty((ncomp,)+ipos.shape[1:])
	iflat = ipos.reshape(ipos.shape[0],-1)
	oflat = opos.reshape(opos.shape[0],-1)
	gflat = grad.reshape(grad.shape[0],-1)
	if geodesic:
		# Loop over chunks in order to conserve memory
		step = 0x100000
		for i in range(0, iflat.shape[1], step):
			# The helper function assumes zenith coordinates
			small_grad = gflat[:,i:i+step].copy(); small_grad[0] = -small_grad[0]
			small_ipos = iflat[:,i:i+step].copy(); small_ipos[0] = np.pi/2-small_ipos[0]
			small_opos, small_orot = offset_by_grad_helper(small_ipos, small_grad, ncomp>2)
			oflat[0,i:i+step] = np.pi/2 - small_opos[0]
			oflat[1,i:i+step] = small_opos[1]
			# Handle rotation if necessary
			if oflat.shape[0] > 2:
				oflat[2,i:i+step] = np.arctan2(small_orot[1],small_orot[0])
				if iflat.shape[0] > 2:
					oflat[2,i:i+step] += iflat[2,i:i+step]
	else:
		oflat[0] = iflat[0] + gflat[0]
		oflat[1] = iflat[1] + gflat[1]/np.cos(iflat[0])
		oflat[:2] = pole_wrap(oflat[:2])
		if oflat.shape[0] > 2: oflat[2] = 0
	return opos

def offset_by_grad_helper(ipos, grad, pol):
	grad = np.array(grad)
	grad[:,np.all(grad==0,0)] = 1e-20
	d = np.sum(grad**2,0)**0.5
	grad  /=d
	cosd, sind = np.cos(d), np.sin(d)
	cost, sint = np.cos(ipos[0]), np.sin(ipos[0])
	ocost  = cosd*cost-sind*sint*grad[0]
	osint  = (1-ocost**2)**0.5
	ophi   = ipos[1] + np.arcsin(sind*grad[1]/osint)
	if not pol:
		return np.array([np.arccos(ocost), ophi]), None
	A      = grad[1]/(sind*cost/sint+grad[0]*cosd)
	nom1   = grad[0]+grad[1]*A
	denom  = 1+A**2
	cosgam = nom1**2/denom-1
	singam = nom1*(grad[1]-grad[0]*A)/denom
	return np.array([np.arccos(ocost), ophi]), np.array([cosgam,singam])

def pole_wrap(pos):
	"""Handle pole wraparound."""
	a = np.array(pos)
	bad = np.where(a[0] > np.pi/2)
	a[0,bad] = np.pi - a[0,bad]
	a[1,bad] = a[1,bad]+np.pi
	bad = np.where(a[0] < -np.pi/2)
	a[0,bad] = -np.pi - a[0,bad]
	a[1,bad] = a[1,bad]+np.pi
	return a
