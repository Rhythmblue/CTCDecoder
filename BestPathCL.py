from __future__ import division
from __future__ import print_function
import os
import numpy as np
import pyopencl as cl


class CLWrapper:
	"class holds information about OpenCL state"
	
	def __init__(self, batchSize, maxT, maxC, kernelVariant = 2, enableGPUDebug = False):
		"specify size: number of batch elements, number of time-steps, number of characters. Set kernelVariant to either 1 or 2. Set enableGPUDebug to True to debug kernel via CodeXL."
	
		# force rebuild of program such that GPU debugger can attach to kernel
		if enableGPUDebug:
			os.environ['PYOPENCL_COMPILER_OUTPUT'] = '1'
			os.environ['PYOPENCL_NO_CACHE'] = '1'	
			
		#consts
		sizeOfInt32 = 4
		sizeOfFloat32 = 4
		self.batchSize = batchSize
		self.maxT = maxT
		self.maxC = maxC
		assert(kernelVariant in [1, 2])
		self.kernelVariant = kernelVariant
	
		# platform, context, queue
		platforms = cl.get_platforms()
		assert(len(platforms) > 0)
		self.platform = platforms[0] # take first platform
		devices = self.platform.get_devices(cl.device_type.GPU) # get GPU devices
		assert(len(devices) > 0)
		self.device = devices[0] # take first GPU
		self.context = cl.Context([self.device]) # context contains the first GPU
		self.queue = cl.CommandQueue(self.context, self.device) # command queue to first GPU
	
		# buffer
		self.batchBuf = cl.Buffer(self.context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=np.zeros([batchSize, maxC, maxT]).astype(np.float32))
		self.res = np.zeros([batchSize, maxT]).astype(np.int32)
		self.resBuf = cl.Buffer(self.context, cl.mem_flags.WRITE_ONLY, self.res.nbytes)
		self.tmpBuf = cl.Buffer(self.context, cl.mem_flags.WRITE_ONLY, self.res.nbytes)
		
		# compile program
		self.program = cl.Program(self.context, open('BestPathCL.cl').read()).build()
	
		# variant 1: single pass 
		if kernelVariant == 1:
			self.kernel1 = cl.Kernel(self.program, 'bestPathAndCollapse')
			self.kernel1.set_arg(0, self.batchBuf)
			self.kernel1.set_arg(1, np.int32(maxT))
			self.kernel1.set_arg(2, np.int32(maxC))
			self.kernel1.set_arg(3, cl.LocalMemory(maxT * sizeOfInt32))
			self.kernel1.set_arg(4, self.resBuf)
		
		# variant 2: two passes
		else:
			# kernel1: calculate best path
			self.kernel1 = cl.Kernel(self.program, 'bestPath')
			self.kernel1.set_arg(0, self.batchBuf)
			self.kernel1.set_arg(1, np.int32(maxT))
			self.kernel1.set_arg(2, np.int32(maxC))
			self.kernel1.set_arg(3, cl.LocalMemory(maxC * sizeOfFloat32))
			self.kernel1.set_arg(4, cl.LocalMemory(maxC * sizeOfInt32))
			self.kernel1.set_arg(5, self.tmpBuf)
			
			# kernel2: collapse best path
			self.kernel2 = cl.Kernel(self.program, 'collapsePath')
			self.kernel2.set_arg(0, self.tmpBuf)
			self.kernel2.set_arg(1, np.int32(maxT))
			self.kernel2.set_arg(2, np.int32(maxC))
			self.kernel2.set_arg(3, self.resBuf)
		

	def compute(self, batch):
		"compute best path for each batch element. Returns blank-terminated label strings for batch elements."
	
		# copy batch to device
		cl.enqueue_write_buffer(self.queue, self.batchBuf, batch.astype(np.float32), is_blocking = False)
		
		# one pass
		if self.kernelVariant == 1:
			cl.enqueue_nd_range_kernel(self.queue, self.kernel1, (self.batchSize, self.maxT), (1, self.maxT))
		# two passes
		else:
			cl.enqueue_nd_range_kernel(self.queue, self.kernel1, (self.batchSize, self.maxT, self.maxC), (1, 1, self.maxC))
			cl.enqueue_nd_range_kernel(self.queue, self.kernel2, (self.batchSize,), None)
			
		# copy result back from GPU and return it
		cl.enqueue_read_buffer(self.queue, self.resBuf, self.res, is_blocking = True)
		return self.res
		

def ctcBestPathCL(batch, classes, clWrapper):
	"implements best path decoding on the GPU with OpenCL"
	
	# compute best labeling
	labelStrBatch = clWrapper.compute(batch)
	
	#go over batch
	blank = len(classes)
	charStrBatch = []
	for b in range(clWrapper.batchSize):
		# map to chars
		charStr = ''
		for label in labelStrBatch[b]:
			if label == blank:
				break
			charStr += classes[label]
		charStrBatch.append(charStr)

	return charStrBatch


if __name__ == '__main__':
	classes = "ab"
	mat = np.array([[0.4, 0, 0.6], [0.4, 0, 0.6]])
	maxT, maxC = mat.shape
	clWrapper = CLWrapper(1, maxT, maxC)
	print('Test best path decoding')
	expected = ''
	actual = ctcBestPathCL(np.stack([mat]), classes, clWrapper)[0]
	print('Expected: "' + expected + '"')
	print('Actual: "' + actual + '"')
	print('OK' if expected == actual else 'ERROR')