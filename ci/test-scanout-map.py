"""Exercise the actual PCI selection and aperture verifier with host-side fakes.

These tests cover bounds/lifetime and ordinary RAM readback, not GPU MMIO.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
rm = (root / "kexts/NVRM/os-xnu.cpp").read_text(encoding="utf-8")
selector = rm[rm.index("static IOMemoryDescriptor *nv_xnu_scanout_resource("):rm.index("void *NV_API_CALL os_map_kernel_space(")]
fb = (root / "kexts/NVRMFB/fb/nvrm-fb.cpp").read_text(encoding="utf-8")
verifier = fb[fb.index("bool NVRMNVDAFramebuffer::verifyAperture("):fb.index("static volatile SInt64 gVramMappedBytes")]
assert 'resourceSelected && !md) return NULL' in rm
assert 'md->map(cache)' in rm
assert 'nv_xnu_map_publish(&nv_xnu_maps[i], md, map, va, start, size, NULL)' in rm
assert verifier.index('control1 != SIG1 || control2 != SIG2') < verifier.index('apertureFor(phys, 0x4000)')
assert 'if (!contig)' in fb and 'SYSMEM' in fb

harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdarg>
#include <vector>
using NvU64 = unsigned long long;
using IOByteCount = unsigned long long;
using addr64_t = unsigned long long;
using IOItemCount = unsigned;
using UInt32 = uint32_t;
enum { kIOMemoryMapperNone = 1, kIODirectionInOut = 3, kIOMapWriteCombineCache = 7, NV_GPU_BAR_INDEX_FB = 1 };
static int kprintf(const char *, ...) { return 0; }
static int live = 0;
static bool failSub = false, failDescriptor = false, failMap = false, mismatchedMap = false;
static UInt32 backing[4096], other[4096];
struct IOMemoryMap {
    uintptr_t getVirtualAddress() { return (uintptr_t)(mismatchedMap ? other : backing); }
    void release() { delete this; }
};
struct IOMemoryDescriptor {
    NvU64 pa, bytes, segment;
    int refs = 1;
    IOMemoryDescriptor *parent = nullptr;
    IOMemoryDescriptor(NvU64 a, NvU64 n) : pa(a), bytes(n), segment(n) { ++live; }
    NvU64 getLength() const { return bytes; }
    NvU64 getPhysicalSegment(unsigned offset, IOByteCount *n, unsigned option) {
        assert(!offset && option == kIOMemoryMapperNone); *n = segment; return pa;
    }
    IOMemoryMap *map(unsigned option) {
        assert(option == kIOMapWriteCombineCache); return failMap ? nullptr : new IOMemoryMap;
    }
    void release() { if (!--refs) { if (parent) parent->release(); --live; delete this; } }
};
using IODeviceMemory = IOMemoryDescriptor;
struct IOSubMemoryDescriptor {
    static IOMemoryDescriptor *withSubRange(IOMemoryDescriptor *p, NvU64 offset, NvU64 n, unsigned direction) {
        assert(direction == kIODirectionInOut);
        assert(offset <= p->bytes && n <= p->bytes - offset);
        if (failSub) return nullptr;
        auto *s = new IOMemoryDescriptor(p->pa + offset, n); s->parent = p; ++p->refs; return s;
    }
};
struct IOPCIDevice {
    std::vector<IOMemoryDescriptor *> memory;
    unsigned getDeviceMemoryCount() { return (unsigned)memory.size(); }
    IOMemoryDescriptor *getDeviceMemoryWithIndex(unsigned i) { return memory.at(i); }
    unsigned configRead16(unsigned offset) { assert(offset == 4); return 0x147; }
};
struct nv_xnu_pci_slot { IOPCIDevice *pci; unsigned domain, bus, slot, function; };
struct nv_state_t {
    struct { unsigned vendor_id, device_id; } pci_info;
    struct { NvU64 cpu_address, size; } bars[3];
    void *handle;
};
static nv_state_t state;
static nv_state_t *nv_xnu_gpu_state() { return &state; }
__SELECTOR__
#define FBLOG(...) do {} while (0)
struct NVRMNVDAFramebuffer {
    void *fKva = backing;
    NvU64 fMemBytes = sizeof backing;
    IODeviceMemory *apertureFor(NvU64, IOByteCount n) {
        return failDescriptor ? nullptr : new IOMemoryDescriptor(0xb2350000, n);
    }
    bool verifyAperture(NvU64, const char *);
};
__VERIFIER__
int main() {
    IOPCIDevice pci;
    nv_xnu_pci_slot slot = { &pci, 0, 0xd5, 0, 0 };
    state.pci_info = { 0x10de, 0x1e30 };
    state.bars[1] = { 0xb0000000, 256ull << 20 }; state.handle = &slot;
    auto *bar = new IOMemoryDescriptor(0xb0000000, 256ull << 20);
    pci.memory = { nullptr, bar };
    bool selected = false;
    auto *s = nv_xnu_scanout_resource(0xb2350000, 0x1fb0000, &selected);
    assert(s && selected && s->pa == 0xb2350000 && s->bytes == 0x1fb0000 && bar->refs == 2);
    s->release(); assert(live == 1 && bar->refs == 1);
    // Last byte is valid; wrapped, empty and out-of-range requests never select the experiment.
    s = nv_xnu_scanout_resource(0xbfffffff, 1, &selected); assert(s && selected); s->release();
    for (const auto &r : std::vector<std::pair<NvU64, NvU64>>{
        {0xafffffff, 1}, {0xbfffffff, 2}, {0xb0000000, 0},
        {0xb0000000, (256ull << 20) + 1}, {~0ull - 4095, 8192},
        {0xc4000000, 4096}}) {
        assert(!nv_xnu_scanout_resource(r.first, r.second, &selected) && !selected);
    }
    state.pci_info.device_id = 0x2684;
    assert(!nv_xnu_scanout_resource(0xb0000000, 4096, &selected) && !selected);
    state.pci_info.device_id = 0x1e30; state.pci_info.vendor_id = 0x8086;
    assert(!nv_xnu_scanout_resource(0xb0000000, 4096, &selected) && !selected);
    state.pci_info.vendor_id = 0x10de;
    state.bars[1].size = 8ull << 30;
    assert(!nv_xnu_scanout_resource(0xb0000000, 4096, &selected) && !selected);
    state.bars[1].size = 256ull << 20;
    // A selected experiment fails closed on a stale, fragmented or missing PCI resource.
    for (int failure = 0; failure < 5; failure++) {
        if (failure == 0) bar->pa++;
        if (failure == 1) bar->bytes--;
        if (failure == 2) bar->segment--;
        if (failure == 3) failSub = true;
        if (failure == 4) state.handle = nullptr;
        assert(!nv_xnu_scanout_resource(0xb2350000, 4096, &selected) && selected);
        bar->pa = 0xb0000000; bar->bytes = bar->segment = 256ull << 20;
        failSub = false; state.handle = &slot;
        assert(live == 1 && bar->refs == 1);
    }
    bar->release(); assert(live == 0);
    NVRMNVDAFramebuffer fb;
    backing[0x1000 / 4] = 0x12345678; backing[0x2000 / 4] = 0xabcdef01;
    for (int outcome = 0; outcome < 4; outcome++) {
        failDescriptor = outcome == 1; failMap = outcome == 2; mismatchedMap = outcome == 3;
        assert(fb.verifyAperture(0xb2350000, "test") == (outcome == 0));
        assert(backing[0x1000 / 4] == 0x12345678 && backing[0x2000 / 4] == 0xabcdef01);
        assert(live == 0);
    }
    assert(!fb.verifyAperture(0, "no-phys"));
    fb.fKva = nullptr; assert(!fb.verifyAperture(0xb2350000, "no-kva"));
}
'''.replace("__SELECTOR__", selector).replace("__VERIFIER__", verifier)
cxx = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")
if not cxx:
    raise SystemExit("C++ compiler unavailable")
test_root = root / "build-out/tests"
test_root.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="scanout-map-", dir=test_root) as directory:
    p = Path(directory)
    source = p / "test.cpp"
    binary = p / ("test.exe" if os.name == "nt" else "test")
    source.write_text(harness, encoding="utf-8")
    subprocess.run([cxx, "-std=c++17", "-Wall", "-Wextra", "-Wno-unused-variable", "-Wno-unused-parameter", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print("PASS: actual PCI selector bounds/device gating/resource failure cleanup; actual aperture verifier normal-RAM success/failure and probe-word restoration. GPU MMIO remains untested.")
