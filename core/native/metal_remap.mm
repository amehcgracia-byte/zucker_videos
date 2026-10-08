/*M///////////////////////////////////////////////////////////////////////////////////////
//
//  IMPORTANT: READ BEFORE DOWNLOADING, COPYING, INSTALLING OR USING.
//
//  By downloading, copying, installing or using the software you agree to this license.
//  If you do not agree to this license, do not download, install,
//  copy or use the software.
//
//
//                           License Agreement
//                For Open Source Computer Vision Library
//
// Copyright (C) 2000-2008, Intel Corporation, all rights reserved.
// Copyright (C) 2009, Willow Garage Inc., all rights reserved.
// Copyright (C) 2014-2015, Itseez Inc., all rights reserved.
// Third party copyrights are property of their respective owners.
//
// Redistribution and use in source and binary forms, with or without modification,
// are permitted provided that the following conditions are met:
//
//   * Redistribution's of source code must retain the above copyright notice,
//     this list of conditions and the following disclaimer.
//
//   * Redistribution's in binary form must reproduce the above copyright notice,
//     this list of conditions and the following disclaimer in the documentation
//     and/or other materials provided with the distribution.
//
//   * The name of the copyright holders may not be used to endorse or promote products
//     derived from this software without specific prior written permission.
//
// This software is provided by the copyright holders and contributors "as is" and
// any express or implied warranties, including, but not limited to, the implied
// warranties of merchantability and fitness for a particular purpose are disclaimed.
// In no event shall the Intel Corporation or contributors be liable for any direct,
// indirect, incidental, special, exemplary, or consequential damages
// (including, but not limited to, procurement of substitute goods or services;
// loss of use, data, or profits; or business interruption) however caused
// and on any theory of liability, whether in contract, strict liability,
// or tort (including negligence or otherwise) arising in any way out of
// the use of this software, even if advised of the possibility of such damage.
//
//M*/

// Metal implementation of the fixed-point OpenCV cubic remap contract.
// Adapted coefficient-table generation; the kernel and bridge are new.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <cstring>
#include <cstdio>
#include <cmath>
#include <algorithm>

static NSString *shader = @R"METAL(
#include <metal_stdlib>
using namespace metal;
struct Shape { uint sw; uint sh; uint width; uint height; };
kernel void remap_cubic(device const uchar *source [[buffer(0)]],
                        device const float *mx [[buffer(1)]],
                        device const float *my [[buffer(2)]],
                        device uchar *out [[buffer(3)]],
                        constant Shape &s [[buffer(4)]],
                        device const short *table [[buffer(5)]],
                        uint2 p [[thread_position_in_grid]]) {
    if (p.x >= s.width || p.y >= s.height) return;
    uint i = p.y*s.width+p.x;
    // OpenCV's INTER_CUBIC lookup uses 32 fractional positions per axis.
    int qx = int(rint(mx[i]*32.0f)), qy = int(rint(my[i]*32.0f));
    int ix = (qx >> 5)-1, iy = (qy >> 5)-1;
    uint offset_table = uint((qy & 31)*32+(qx & 31))*16;
    int3 sum = int3(16384);
    for (int y=0; y<4; ++y) {
        int yy = ((iy+y)%int(s.sh)+int(s.sh))%int(s.sh);
        for (int x=0; x<4; ++x) {
            int xx = ((ix+x)%int(s.sw)+int(s.sw))%int(s.sw);
            uint offset = (uint(yy)*s.sw+uint(xx))*3;
            int weight = int(table[offset_table+y*4+x]);
            sum += int3(source[offset],source[offset+1],source[offset+2])*weight;
        }
    }
    for (uint c=0;c<3;++c) out[i*3+c] = uchar(clamp(sum[c] >> 15,0,255));
}
)METAL";

@interface RemapContext : NSObject
@property id<MTLDevice> device;
@property id<MTLCommandQueue> queue;
@property id<MTLComputePipelineState> pipeline;
@property id<MTLBuffer> source;
@property id<MTLBuffer> mx;
@property id<MTLBuffer> my;
@property id<MTLBuffer> output;
@property id<MTLBuffer> table;
@property NSUInteger sw;
@property NSUInteger sh;
@property NSUInteger width;
@property NSUInteger height;
@end
@implementation RemapContext
@end

// Reproduce the fixed-point cubic lookup contract of OpenCV 4.11 imgwarp.cpp
// (license above), including its coefficient-sum correction. Reference:
// https://github.com/opencv/opencv/blob/4.11.0/modules/imgproc/src/imgwarp.cpp
static void fill_table(short *table) {
    float axis[32][4];
    for (int phase=0;phase<32;++phase) {
        float x=float(phase)/32, a=-.75f, one=1-x;
        axis[phase][0]=((a*(x+1)-5*a)*(x+1)+8*a)*(x+1)-4*a;
        axis[phase][1]=((a+2)*x-(a+3))*x*x+1;
        axis[phase][2]=((a+2)*one-(a+3))*one*one+1;
        axis[phase][3]=1-axis[phase][0]-axis[phase][1]-axis[phase][2];
    }
    for (int fy=0;fy<32;++fy) for (int fx=0;fx<32;++fx) {
        short *weights=table+(fy*32+fx)*16;
        int sum=0;
        for (int y=0;y<4;++y) for (int x=0;x<4;++x) {
            int value=int(std::nearbyint((axis[fy][y]*axis[fx][x])*32768.f));
            weights[y*4+x]=short(std::max(-32768,std::min(32767,value)));
            sum+=weights[y*4+x];
        }
        int low=10, high=10;
        for (int y=2;y<4;++y) for (int x=2;x<4;++x) {
            int i=y*4+x;
            if (weights[i]<weights[low]) low=i;
            else if (weights[i]>weights[high]) high=i;
        }
        int diff=sum-32768;
        weights[diff<0?high:low]=short(weights[diff<0?high:low]-diff);
    }
}

extern "C" void *remap_create(unsigned sw, unsigned sh, unsigned width, unsigned height, char *error, size_t cap) {
    @autoreleasepool {
        RemapContext *c = [RemapContext new];
        c.device = MTLCreateSystemDefaultDevice();
        if (!c.device) { snprintf(error,cap,"No Metal device"); return nullptr; }
        NSError *e = nil;
        MTLCompileOptions *options = [MTLCompileOptions new];
        options.fastMathEnabled = NO;
        id<MTLLibrary> library = [c.device newLibraryWithSource:shader options:options error:&e];
        if (!library) { snprintf(error,cap,"%s",e.localizedDescription.UTF8String); return nullptr; }
        c.pipeline = [c.device newComputePipelineStateWithFunction:[library newFunctionWithName:@"remap_cubic"] error:&e];
        if (!c.pipeline) { snprintf(error,cap,"%s",e.localizedDescription.UTF8String); return nullptr; }
        c.queue = [c.device newCommandQueue];
        c.sw=sw; c.sh=sh; c.width=width; c.height=height;
        c.source = [c.device newBufferWithLength:size_t(sw)*sh*3 options:MTLResourceStorageModeShared];
        c.mx = [c.device newBufferWithLength:size_t(width)*height*4 options:MTLResourceStorageModeShared];
        c.my = [c.device newBufferWithLength:size_t(width)*height*4 options:MTLResourceStorageModeShared];
        c.output = [c.device newBufferWithLength:size_t(width)*height*3 options:MTLResourceStorageModeShared];
        c.table = [c.device newBufferWithLength:32*32*16*sizeof(short) options:MTLResourceStorageModeShared];
        if (!c.queue || !c.source || !c.mx || !c.my || !c.output || !c.table) { snprintf(error,cap,"Metal allocation failed"); return nullptr; }
        fill_table(static_cast<short *>(c.table.contents));
        return (__bridge_retained void *)c;
    }
}

extern "C" int remap_frame(void *opaque, const void *source, const void *mx, const void *my, void *output, char *error, size_t cap) {
    @autoreleasepool {
        RemapContext *c = (__bridge RemapContext *)opaque;
        memcpy(c.source.contents,source,c.source.length);
        memcpy(c.mx.contents,mx,c.mx.length);
        memcpy(c.my.contents,my,c.my.length);
        id<MTLCommandBuffer> command = [c.queue commandBuffer];
        id<MTLComputeCommandEncoder> encoder = [command computeCommandEncoder];
        [encoder setComputePipelineState:c.pipeline];
        [encoder setBuffer:c.source offset:0 atIndex:0];
        [encoder setBuffer:c.mx offset:0 atIndex:1];
        [encoder setBuffer:c.my offset:0 atIndex:2];
        [encoder setBuffer:c.output offset:0 atIndex:3];
        unsigned shape[4] = {unsigned(c.sw),unsigned(c.sh),unsigned(c.width),unsigned(c.height)};
        [encoder setBytes:shape length:sizeof(shape) atIndex:4];
        [encoder setBuffer:c.table offset:0 atIndex:5];
        [encoder dispatchThreads:MTLSizeMake(c.width,c.height,1) threadsPerThreadgroup:MTLSizeMake(16,16,1)];
        [encoder endEncoding];
        dispatch_semaphore_t finished = dispatch_semaphore_create(0);
        [command addCompletedHandler:^(id<MTLCommandBuffer> _) { dispatch_semaphore_signal(finished); }];
        [command commit];
        if (dispatch_semaphore_wait(finished, dispatch_time(DISPATCH_TIME_NOW, 15*NSEC_PER_SEC))) {
            snprintf(error,cap,"Metal command did not complete within 15 seconds"); return 1;
        }
        if (command.status == MTLCommandBufferStatusError) { snprintf(error,cap,"%s",command.error.localizedDescription.UTF8String); return 1; }
        memcpy(output,c.output.contents,c.output.length);
        return 0;
    }
}

extern "C" void remap_destroy(void *opaque) {
    if (opaque) { id value = CFBridgingRelease(opaque); (void)value; }
}
