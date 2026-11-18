// Qualcomm V4L2 (msm_vidc) H.264/H.265 decoder for phone projection video, loaded with ctypes.
// One access unit in, NV12 frames out in the Venus layout VisionIPC uses. Buffer and event
// handling follow system/loggerd/encoder/v4l_decoder.cc; ION handling follows starport_encoder.cc.
#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <fcntl.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <linux/ion.h>
#include <linux/msm_ion.h>
#include <media/msm_media_info.h>
#include <linux/v4l2-controls.h>
#include <linux/videodev2.h>

namespace {
constexpr unsigned EVENT_START = V4L2_EVENT_PRIVATE_START + 0x00001000;
constexpr unsigned EVENT_FLUSH_DONE = EVENT_START + 1;
constexpr unsigned EVENT_INSUFFICIENT = EVENT_START + 3;
constexpr unsigned CID_BASE = 0x00992000;
constexpr unsigned CID_OUTPUT_MODE = CID_BASE + 22;
constexpr unsigned CID_DPB_COLOR_FORMAT = CID_BASE + 44;
constexpr unsigned CMD_FLUSH = 4;
constexpr unsigned FLUSH_CAPTURE = 1 << 1;
constexpr int INPUT_COUNT = 8;
constexpr int OUTPUT_COUNT = 12;

void check(bool ok, const char *what) {
  if (!ok) throw std::runtime_error(std::string(what) + ": " + strerror(errno));
}
void call(int fd, unsigned long request, void *arg, const char *what) {
  int rc;
  do { rc = ioctl(fd, request, arg); } while (rc < 0 && errno == EINTR);
  check(rc >= 0, what);
}
void error_text(char *error, size_t size, const std::exception &e) {
  if (error && size) snprintf(error, size, "%s", e.what());
}

struct Buffer {
  int ion = -1, fd = -1, handle = 0;
  void *addr = MAP_FAILED;
  size_t size = 0;
  Buffer() = default;
  Buffer(const Buffer &) = delete;
  Buffer &operator=(const Buffer &) = delete;
  void allocate(size_t length) {
    size = (length + 4095) & ~size_t(4095);
    ion = open("/dev/ion", O_RDWR | O_CLOEXEC);
    check(ion >= 0, "open ion");
    ion_allocation_data alloc = {};
    alloc.len = size; alloc.align = 4096;
    alloc.heap_id_mask = 1 << ION_IOMMU_HEAP_ID;
    alloc.flags = ION_FLAG_CACHED;
    call(ion, ION_IOC_ALLOC, &alloc, "ION allocate");
    handle = alloc.handle;
    ion_fd_data share = {}; share.handle = handle;
    call(ion, ION_IOC_SHARE, &share, "ION share");
    fd = share.fd;
    check(fcntl(fd, F_SETFD, FD_CLOEXEC) >= 0, "ION close-on-exec");
    addr = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    check(addr != MAP_FAILED, "ION mmap");
  }
  void sync(bool from_device, size_t length = 0) {
    ion_flush_data flush = {};
    flush.handle = handle; flush.vaddr = addr;
    flush.length = length ? std::min(size, (length + 63) & ~size_t(63)) : size;
    ion_custom_data custom = {};
    custom.cmd = from_device ? ION_IOC_INV_CACHES : ION_IOC_CLEAN_CACHES;
    custom.arg = reinterpret_cast<unsigned long>(&flush);
    call(ion, ION_IOC_CUSTOM, &custom, "ION cache sync");
  }
  ~Buffer() {
    if (addr != MAP_FAILED) munmap(addr, size);
    if (fd >= 0) close(fd);
    if (handle) { ion_handle_data h = {}; h.handle = handle; ioctl(ion, ION_IOC_FREE, &h); }
    if (ion >= 0) close(ion);
  }
};

struct Decoder {
  int fd = -1;
  unsigned width, height, stride = 0, uv_offset = 0, frame_size = 0;
  uint32_t codec;
  bool input_on = false, output_on = false, reconfiguring = false;
  Buffer inputs[INPUT_COUNT];
  bool input_busy[INPUT_COUNT] = {};
  std::vector<std::unique_ptr<Buffer>> outputs;

  Decoder(uint32_t fourcc, unsigned w, unsigned h) : width(w), height(h), codec(fourcc) {}
  ~Decoder() {
    if (fd >= 0) {
      v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
      if (input_on) ioctl(fd, VIDIOC_STREAMOFF, &type);
      type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
      if (output_on) ioctl(fd, VIDIOC_STREAMOFF, &type);
      close(fd);
    }
  }

  void requeue_output(unsigned index) {
    Buffer &b = *outputs[index];
    v4l2_plane plane = {};
    plane.m.userptr = reinterpret_cast<unsigned long>(b.addr);
    plane.length = plane.bytesused = b.size;
    plane.reserved[0] = b.fd;
    v4l2_buffer buf = {};
    buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; buf.memory = V4L2_MEMORY_USERPTR;
    buf.index = index; buf.m.planes = &plane; buf.length = 1;
    call(fd, VIDIOC_QBUF, &buf, "queue frame buffer");
  }

  void start_output() {
    v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    if (output_on) {
      call(fd, VIDIOC_STREAMOFF, &type, "stop frames");
      v4l2_requestbuffers req = {}; req.type = type; req.memory = V4L2_MEMORY_USERPTR; req.count = 0;
      call(fd, VIDIOC_REQBUFS, &req, "release frame buffers");
      outputs.clear();
      output_on = false;
    }
    v4l2_ext_control controls[2] = {};
    controls[0].id = CID_OUTPUT_MODE; controls[0].value = 1;        // secondary (linear) output
    controls[1].id = CID_DPB_COLOR_FORMAT; controls[1].value = 0;
    v4l2_ext_controls ext = {};
    ext.count = 2; ext.ctrl_class = V4L2_CTRL_CLASS_MPEG; ext.controls = controls;
    call(fd, VIDIOC_S_EXT_CTRLS, &ext, "output mode");
    v4l2_format fmt = {};
    fmt.type = type;
    fmt.fmt.pix_mp.width = width; fmt.fmt.pix_mp.height = height;
    fmt.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12;
    call(fd, VIDIOC_S_FMT, &fmt, "frame format");
    v4l2_requestbuffers req = {}; req.type = type; req.memory = V4L2_MEMORY_USERPTR; req.count = OUTPUT_COUNT;
    call(fd, VIDIOC_REQBUFS, &req, "frame buffers");
    call(fd, VIDIOC_G_FMT, &fmt, "read frame format");
    stride = VENUS_Y_STRIDE(COLOR_FMT_NV12, width);
    uv_offset = stride * VENUS_Y_SCANLINES(COLOR_FMT_NV12, height);
    frame_size = VENUS_BUFFER_SIZE(COLOR_FMT_NV12, width, height);
    size_t length = std::max<size_t>(frame_size, fmt.fmt.pix_mp.plane_fmt[0].sizeimage);
    for (unsigned i = 0; i < req.count; i++) {
      outputs.emplace_back(new Buffer());
      outputs.back()->allocate(length);
    }
    call(fd, VIDIOC_STREAMON, &type, "start frames");
    output_on = true;
    for (unsigned i = 0; i < outputs.size(); i++) requeue_output(i);
  }

  void init() {
    fd = open("/dev/video32", O_RDWR | O_NONBLOCK | O_CLOEXEC);
    check(fd >= 0, "open hardware decoder");
    v4l2_capability cap = {};
    call(fd, VIDIOC_QUERYCAP, &cap, "decoder capability");
    if (strcmp(reinterpret_cast<char *>(cap.card), "msm_vidc_vdec")) throw std::runtime_error("Unsupported decoder driver");
    for (unsigned event : {EVENT_FLUSH_DONE, EVENT_INSUFFICIENT}) {
      v4l2_event_subscription sub = {}; sub.type = event;
      call(fd, VIDIOC_SUBSCRIBE_EVENT, &sub, "subscribe event");
    }
    v4l2_format fmt = {};
    fmt.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    fmt.fmt.pix_mp.width = width; fmt.fmt.pix_mp.height = height; fmt.fmt.pix_mp.pixelformat = codec;
    call(fd, VIDIOC_S_FMT, &fmt, "stream format");
    size_t packet = fmt.fmt.pix_mp.plane_fmt[0].sizeimage;
    for (auto &b : inputs) b.allocate(packet);
    v4l2_streamparm parm = {};
    parm.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    parm.parm.output.timeperframe = {1, 60};
    call(fd, VIDIOC_S_PARM, &parm, "frame rate");
    v4l2_requestbuffers req = {};
    req.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; req.memory = V4L2_MEMORY_USERPTR; req.count = INPUT_COUNT;
    call(fd, VIDIOC_REQBUFS, &req, "stream buffers");
    v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    call(fd, VIDIOC_STREAMON, &type, "start stream");
    input_on = true;
    start_output();
  }

  void reclaim_inputs() {
    while (true) {
      v4l2_plane plane = {};
      v4l2_buffer buf = {};
      buf.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; buf.memory = V4L2_MEMORY_USERPTR;
      buf.m.planes = &plane; buf.length = 1;
      if (ioctl(fd, VIDIOC_DQBUF, &buf) < 0) {
        if (errno == EAGAIN || errno == EINTR) return;
        check(false, "reclaim stream buffer");
      }
      if (buf.index < INPUT_COUNT) input_busy[buf.index] = false;
    }
  }

  void handle_events() {
    while (true) {
      v4l2_event event = {};
      if (ioctl(fd, VIDIOC_DQEVENT, &event) < 0) {
        if (errno == EAGAIN || errno == ENOENT || errno == EINTR) return;
        check(false, "decoder event");
      }
      const unsigned *data = reinterpret_cast<const unsigned *>(event.u.data);
      if (event.type == EVENT_INSUFFICIENT) {
        height = data[0]; width = data[1];
        v4l2_decoder_cmd cmd = {}; cmd.cmd = CMD_FLUSH; cmd.flags = FLUSH_CAPTURE;
        call(fd, VIDIOC_DECODER_CMD, &cmd, "flush frames");
        reconfiguring = true;
      } else if (event.type == EVENT_FLUSH_DONE && (data[0] & FLUSH_CAPTURE) && reconfiguring) {
        start_output();
        reconfiguring = false;
      }
    }
  }

  int push(const unsigned char *data, size_t length, int64_t timestamp) {
    reclaim_inputs();
    int index = -1;
    for (int i = 0; i < INPUT_COUNT; i++) if (!input_busy[i]) { index = i; break; }
    if (index < 0) return 0;
    Buffer &b = inputs[index];
    if (length > b.size) throw std::runtime_error("Access unit larger than the stream buffer");
    memcpy(b.addr, data, length);
    b.sync(false, length);
    v4l2_plane plane = {};
    plane.m.userptr = reinterpret_cast<unsigned long>(b.addr);
    plane.length = b.size; plane.bytesused = length;
    plane.reserved[0] = b.fd;
    v4l2_buffer buf = {};
    buf.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE; buf.memory = V4L2_MEMORY_USERPTR;
    buf.index = index; buf.m.planes = &plane; buf.length = 1;
    buf.flags = V4L2_BUF_FLAG_TIMESTAMP_COPY;
    buf.timestamp.tv_sec = timestamp / 1000000; buf.timestamp.tv_usec = timestamp % 1000000;
    call(fd, VIDIOC_QBUF, &buf, "queue access unit");
    input_busy[index] = true;
    return 1;
  }

  // Copies only the newest decoded frame into out and returns how many were decoded; older ones go straight back.
  int pull(int timeout_ms, unsigned char *out, size_t capacity, int *info, int64_t *timestamp) {
    pollfd p = {fd, POLLIN | POLLRDNORM | POLLPRI, 0};
    int rc;
    do { rc = poll(&p, 1, timeout_ms); } while (rc < 0 && errno == EINTR);
    check(rc >= 0, "poll decoder");
    handle_events();
    reclaim_inputs();
    int decoded = 0, newest = -1;
    timeval newest_time = {};
    while (!reconfiguring && output_on) {
      v4l2_plane plane = {};
      v4l2_buffer buf = {};
      buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE; buf.memory = V4L2_MEMORY_USERPTR;
      buf.m.planes = &plane; buf.length = 1;
      if (ioctl(fd, VIDIOC_DQBUF, &buf) < 0) {
        if (errno == EAGAIN || errno == EINTR) break;
        check(false, "dequeue frame");
      }
      if (plane.bytesused && buf.index < outputs.size()) {
        if (newest >= 0) requeue_output(newest);
        newest = buf.index; newest_time = buf.timestamp;
        decoded++;
      } else {
        requeue_output(buf.index);
      }
    }
    if (newest >= 0) {
      Buffer &b = *outputs[newest];
      if (capacity < frame_size) { requeue_output(newest); throw std::runtime_error("Frame buffer too small"); }
      b.sync(true, frame_size);
      memcpy(out, b.addr, frame_size);
      info[0] = width; info[1] = height; info[2] = stride; info[3] = uv_offset; info[4] = frame_size;
      *timestamp = int64_t(newest_time.tv_sec) * 1000000 + newest_time.tv_usec;
      requeue_output(newest);
    }
    return decoded;
  }
};
}  // namespace

extern "C" {
int projection_decoder_abi() { return 1; }

void projection_decoder_nv12_layout(int width, int height, int *info) {
  info[0] = width; info[1] = height;
  info[2] = VENUS_Y_STRIDE(COLOR_FMT_NV12, width);
  info[3] = info[2] * VENUS_Y_SCANLINES(COLOR_FMT_NV12, height);
  info[4] = VENUS_BUFFER_SIZE(COLOR_FMT_NV12, width, height);
}

void *projection_decoder_create(int hevc, int width, int height, char *error, size_t size) {
  try {
    if (width < 64 || height < 64 || width > 4096 || height > 4096) throw std::runtime_error("Unsupported video size");
    auto decoder = std::make_unique<Decoder>(hevc ? V4L2_PIX_FMT_HEVC : V4L2_PIX_FMT_H264, width, height);
    decoder->init();
    return decoder.release();
  } catch (const std::exception &e) { error_text(error, size, e); return nullptr; }
}

int projection_decoder_layout(void *ptr, int *info) {
  auto *d = static_cast<Decoder *>(ptr);
  info[0] = d->width; info[1] = d->height; info[2] = d->stride; info[3] = d->uv_offset; info[4] = d->frame_size;
  return 0;
}

int projection_decoder_push(void *ptr, const unsigned char *data, size_t length, long long timestamp, char *error, size_t size) {
  try { return static_cast<Decoder *>(ptr)->push(data, length, timestamp); }
  catch (const std::exception &e) { error_text(error, size, e); return -1; }
}

int projection_decoder_pull(void *ptr, int timeout_ms, unsigned char *out, size_t capacity, int *info, long long *timestamp,
                            char *error, size_t size) {
  try {
    int64_t ts = 0;
    int got = static_cast<Decoder *>(ptr)->pull(timeout_ms, out, capacity, info, &ts);
    *timestamp = ts;
    return got;
  } catch (const std::exception &e) { error_text(error, size, e); return -1; }
}

void projection_decoder_destroy(void *ptr) { delete static_cast<Decoder *>(ptr); }
}
