# Copyright (c) 2012, 2013, 2014 James Hensman
# Licensed under the GPL v3 (see LICENSE.txt)

import numpy as np
from utilities import multiple_pdinv, lngammad
from scipy.special import gammaln, digamma
from scipy import stats
from collapsed_mixture import CollapsedMixture

class MOG(CollapsedMixture):
    """
    A Mixture of Gaussians, using the fast variational framework

    Arguments
    =========
    X - a np.array of the observed data: each row contains one datum.
    K - the number of clusters (or initial number of clusters in the Dirichlet Process case)
    alpha - the A priori dirichlet concentrationn parameter
    prior_Z  - either 'symmetric' or 'dp', specifies whether to use a symmetric dirichelt prior for the clusters, or a (truncated) Dirichlet Process.
    name - a convenient string for printing the model (default MOG)

    Optional arguments for the parameters of the Gaussian-Wishart priors on the clusters
    prior_m - prior mean
    prior_kappa - prior connectivity
    prior_S - prior Wishart covariance
    prior_v - prior Wishart degrees of freedom

    """
    def __init__(self, X, K=2, prior_Z='symmetric', alpha=10., prior_m=None, prior_kappa=None, prior_S=None, prior_v=None, name='MOG'):
        self.X = X
        self.N, self.D = X.shape

        # store the prior cluster parameters
        self.m0 = self.X.mean(0) if prior_m is None else prior_m
        self.k0 = prior_kappa or 1e-6
        self.S0 = np.eye(self.D)*1e-3 if prior_S is None else prior_S
        self.v0 = prior_v or self.D+1.

        #precomputed stuff
        self.k0m0m0T = self.k0*self.m0[:,np.newaxis]*self.m0[np.newaxis,:]
        self.XXT = self.X[:,:,np.newaxis]*self.X[:,np.newaxis,:]
        self.S0_halflogdet = np.sum(np.log(np.sqrt(np.diag(np.linalg.cholesky(self.S0)))))

        CollapsedMixture.__init__(self, self.N, K, prior_Z, alpha, name=name)
        self.do_computations()

    def do_computations(self):
        #computations needed for bound, gradient and predictions
        self.kNs = self.phi_hat + self.k0
        self.vNs = self.phi_hat + self.v0
        self.Xsumk = np.tensordot(self.X,self.phi,((0),(0))) #D x K
        Ck = np.tensordot(self.phi, self.XXT,((0),(0))).T# D x D x K
        self.mun = (self.k0*self.m0[:,None] + self.Xsumk)/self.kNs[None,:] # D x K
        self.munmunT = self.mun[:,None,:]*self.mun[None,:,:]
        self.Sns = self.S0[:,:,None] + Ck + self.k0m0m0T[:,:,None] - self.kNs[None,None,:]*self.munmunT
        self.Sns_inv, self.Sns_halflogdet = multiple_pdinv(self.Sns)

    def bound(self):
        """Compute the lower bound on the model evidence.  """
        return -0.5*self.D*np.sum(np.log(self.kNs/self.k0))\
            +self.K*self.v0*self.S0_halflogdet - np.sum(self.vNs*self.Sns_halflogdet)\
            +np.sum(lngammad(self.vNs, self.D))- self.K*lngammad(self.v0, self.D)\
            +self.mixing_prop_bound()\
            +self.H\
            -0.5*self.N*self.D*np.log(np.pi)

    def vb_grad_natgrad(self):
        """Gradients of the bound"""
        x_m = self.X[:,:,None]-self.mun[None,:,:]
        dS = x_m[:,:,None,:]*x_m[:,None,:,:]
        SnidS = self.Sns_inv[None,:,:,:]*dS
        dlndtS_dphi = np.dot(np.ones(self.D), np.dot(np.ones(self.D), SnidS))

        grad_phi =  (-0.5*self.D/self.kNs + 0.5*digamma((self.vNs-np.arange(self.D)[:,None])/2.).sum(0) + self.mixing_prop_bound_grad() - self.Sns_halflogdet -1.) + (self.Hgrad-0.5*dlndtS_dphi*self.vNs)

        natgrad = grad_phi - np.sum(self.phi*grad_phi, 1)[:,None] # corrects for softmax (over) parameterisation
        grad = natgrad*self.phi

        return grad.flatten(), natgrad.flatten()


    def predict_components_ln(self, Xnew):
        """The predictive density under each component"""
        Dist = Xnew[:,:,np.newaxis]-self.mun[np.newaxis,:,:] # Nnew x D x K
        tmp = np.sum(Dist[:,:,None,:]*self.Sns_inv[None,:,:,:],1)#*(kn+1.)/(kn*(vn-self.D+1.))
        mahalanobis = np.sum(tmp*Dist, 1)/(self.kNs+1.)*self.kNs*(self.vNs-self.D+1.)
        halflndetSigma = self.Sns_halflogdet + 0.5*self.D*np.log((self.kNs+1.)/(self.kNs*(self.vNs-self.D+1.)))

        Z  = gammaln(0.5*(self.vNs[np.newaxis,:]+1.))\
            -gammaln(0.5*(self.vNs[np.newaxis,:]-self.D+1.))\
            -(0.5*self.D)*(np.log(self.vNs[np.newaxis,:]-self.D+1.) + np.log(np.pi))\
            - halflndetSigma \
            - (0.5*(self.vNs[np.newaxis,:]+1.))*np.log(1.+mahalanobis/(self.vNs[np.newaxis,:]-self.D+1.))
        return Z

    def predict_components(self, Xnew):
        return np.exp(self.predict_components_ln(Xnew))

    def predict(self, Xnew):
        Z = self.predict_components(Xnew)
        #calculate the weights for each component
        phi_hat = self.phi.sum(0)
        pi = phi_hat+self.alpha
        pi /= pi.sum()
        Z *= pi[np.newaxis,:]
        return Z.sum(1)

    def plot(self, newfig=True):
        from matplotlib import pyplot as plt
        if self.X.shape[1]==2:
            if newfig:plt.figure()
            xmin, ymin = self.X.min(0)
            xmax, ymax = self.X.max(0)
            xmin, xmax = xmin-0.1*(xmax-xmin), xmax+0.1*(xmax-xmin)
            ymin, ymax = ymin-0.1*(ymax-ymin), ymax+0.1*(ymax-ymin)
            xx, yy = np.mgrid[xmin:xmax:100j, ymin:ymax:100j]
            Xgrid = np.vstack((xx.flatten(), yy.flatten())).T
            zz = self.predict(Xgrid).reshape(100, 100)
            zz_data = self.predict(self.X)
            plt.contour(xx, yy, zz, [stats.scoreatpercentile(zz_data, 5)], colors='k', linewidths=3)
            plt.scatter(self.X[:,0], self.X[:,1], 30, np.argmax(self.phi, 1), linewidth=0, cmap=plt.cm.gist_rainbow)

            zz_components = self.predict_components(Xgrid)
            phi_hat = self.phi.sum(0)
            pi = phi_hat+self.alpha
            pi /= pi.sum()
            zz_components *= pi[np.newaxis,:]
            [plt.contour(xx, yy, zz.reshape(100, 100), [stats.scoreatpercentile(zz_data, 5.)], colors='k', linewidths=1) for zz in zz_components.T]

