import React, { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/use-toast";
import { apiBase } from "../lib/apiBase";

const OPENCV_URL = "https://docs.opencv.org/4.x/opencv.js";
const PDF_LIB_URL = "https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js";
let opencvPromise;
let pdfLibPromise;

function loadOpenCv() {
  if (typeof window === "undefined") return Promise.reject(new Error("window_unavailable"));
  if (window.cv && window.cv.Mat) return Promise.resolve(window.cv);
  if (opencvPromise) return opencvPromise;
  opencvPromise = new Promise((resolve, reject) => {
    const existing = document.getElementById("opencvjs");
    if (existing) {
      const check = () => {
        if (window.cv && window.cv.Mat) return resolve(window.cv);
        setTimeout(check, 50);
        return null;
      };
      check();
      return;
    }
    const script = document.createElement("script");
    script.id = "opencvjs";
    script.async = true;
    script.src = OPENCV_URL;
    script.onload = () => {
      if (window.cv && window.cv.Mat) {
        resolve(window.cv);
        return;
      }
      if (window.cv) {
        window.cv.onRuntimeInitialized = () => resolve(window.cv);
      } else {
        reject(new Error("opencv_failed_to_load"));
      }
    };
    script.onerror = () => reject(new Error("opencv_load_error"));
    document.body.appendChild(script);
  });
  return opencvPromise;
}

function loadPdfLib() {
  if (typeof window === "undefined") return Promise.reject(new Error("window_unavailable"));
  if (window.PDFLib?.PDFDocument) return Promise.resolve(window.PDFLib);
  if (pdfLibPromise) return pdfLibPromise;
  pdfLibPromise = new Promise((resolve, reject) => {
    const existing = document.getElementById("pdflibjs");
    if (existing) {
      const check = () => {
        if (window.PDFLib?.PDFDocument) return resolve(window.PDFLib);
        setTimeout(check, 50);
        return null;
      };
      check();
      return;
    }
    const script = document.createElement("script");
    script.id = "pdflibjs";
    script.async = true;
    script.src = PDF_LIB_URL;
    script.onload = () => {
      if (window.PDFLib?.PDFDocument) {
        resolve(window.PDFLib);
        return;
      }
      reject(new Error("pdf_lib_failed_to_load"));
    };
    script.onerror = () => reject(new Error("pdf_lib_load_error"));
    document.body.appendChild(script);
  });
  return pdfLibPromise;
}

function scanLabel(mode) {
  if (mode === "master_key") return "Master key";
  if (mode === "student") return "Student scan";
  return "Scan";
}

function orderPoints(points) {
  const rect = [null, null, null, null];
  const sum = points.map((p) => p.x + p.y);
  const diff = points.map((p) => p.y - p.x);
  rect[0] = points[sum.indexOf(Math.min(...sum))];
  rect[2] = points[sum.indexOf(Math.max(...sum))];
  rect[1] = points[diff.indexOf(Math.min(...diff))];
  rect[3] = points[diff.indexOf(Math.max(...diff))];
  return rect;
}

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

async function canvasToBlob(canvas, type = "image/jpeg", quality = 0.92) {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), type, quality);
  });
}

async function rectifyWithOpenCv(canvas, cv) {
  const src = cv.imread(canvas);
  const gray = new cv.Mat();
  const blurred = new cv.Mat();
  const edged = new cv.Mat();
  const contours = new cv.MatVector();
  const hierarchy = new cv.Mat();
  const kernel = cv.Mat.ones(3, 3, cv.CV_8U);
  let best = null;

  try {
    cv.cvtColor(src, gray, cv.COLOR_RGBA2GRAY);
    cv.GaussianBlur(gray, blurred, new cv.Size(5, 5), 0);
    cv.Canny(blurred, edged, 50, 150);
    cv.dilate(edged, edged, kernel);
    cv.erode(edged, edged, kernel);
    cv.findContours(edged, contours, hierarchy, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE);

    let maxArea = 0;
    for (let i = 0; i < contours.size(); i += 1) {
      const contour = contours.get(i);
      const peri = cv.arcLength(contour, true);
      const approx = new cv.Mat();
      cv.approxPolyDP(contour, approx, 0.02 * peri, true);
      if (approx.rows === 4) {
        const area = cv.contourArea(approx);
        if (area > maxArea) {
          maxArea = area;
          if (best) best.delete();
          best = approx.clone();
        }
      }
      approx.delete();
      contour.delete();
    }

    let result = src;
    let warped = null;
    if (best && best.rows === 4) {
      const data = best.data32S;
      if (data && data.length >= 8) {
        const points = [];
        for (let i = 0; i < 4; i += 1) {
          points.push({ x: data[i * 2], y: data[i * 2 + 1] });
        }
        const [tl, tr, br, bl] = orderPoints(points);
        const maxWidth = Math.max(distance(br, bl), distance(tr, tl));
        const maxHeight = Math.max(distance(tr, br), distance(tl, bl));
        if (maxWidth > 200 && maxHeight > 200) {
          const dst = cv.matFromArray(4, 1, cv.CV_32FC2, [
            0, 0,
            maxWidth - 1, 0,
            maxWidth - 1, maxHeight - 1,
            0, maxHeight - 1,
          ]);
          const srcTri = cv.matFromArray(4, 1, cv.CV_32FC2, [
            tl.x, tl.y,
            tr.x, tr.y,
            br.x, br.y,
            bl.x, bl.y,
          ]);
          const matrix = cv.getPerspectiveTransform(srcTri, dst);
          warped = new cv.Mat();
          cv.warpPerspective(src, warped, matrix, new cv.Size(maxWidth, maxHeight), cv.INTER_LINEAR, cv.BORDER_REPLICATE);
          result = warped;
          dst.delete();
          srcTri.delete();
          matrix.delete();
        }
      }
    }

    const outCanvas = document.createElement("canvas");
    outCanvas.width = result.cols;
    outCanvas.height = result.rows;
    cv.imshow(outCanvas, result);
    if (warped) warped.delete();
    const blob = await canvasToBlob(outCanvas);
    return {
      blob,
      width: outCanvas.width,
      height: outCanvas.height,
      previewUrl: blob ? URL.createObjectURL(blob) : "",
    };
  } finally {
    if (best) best.delete();
    kernel.delete();
    hierarchy.delete();
    contours.delete();
    edged.delete();
    blurred.delete();
    gray.delete();
    src.delete();
  }
}

async function captureFromVideo(video, cvReady) {
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth || 0;
  canvas.height = video.videoHeight || 0;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

  if (cvReady) {
    try {
      const cv = await loadOpenCv();
      return await rectifyWithOpenCv(canvas, cv);
    } catch (err) {
      console.error("OpenCV rectification failed", err);
    }
  }

  const blob = await canvasToBlob(canvas);
  return {
    blob,
    width: canvas.width,
    height: canvas.height,
    previewUrl: blob ? URL.createObjectURL(blob) : "",
  };
}

export default function ScanCapturePage() {
  const { token } = useParams();
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const { toast } = useToast();
  const [mode, setMode] = useState("");
  const [status, setStatus] = useState("pending");
  const [message, setMessage] = useState("");
  const [uploading, setUploading] = useState(false);
  const [cameraError, setCameraError] = useState("");
  const [cameraNotice, setCameraNotice] = useState("");
  const [cvReady, setCvReady] = useState(false);
  const [cvError, setCvError] = useState("");
  const [captured, setCaptured] = useState(null);
  const [pages, setPages] = useState([]);
  const pagesRef = useRef([]);
  const capturedRef = useRef(null);

  useEffect(() => {
    let active = true;
    loadOpenCv()
      .then(() => {
        if (!active) return;
        setCvReady(true);
      })
      .catch((err) => {
        if (!active) return;
        setCvError(err?.message || "OpenCV failed to load");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!token) return;
    const fetchStatus = async () => {
      try {
        const resp = await fetch(`${apiBase()}/api/scan/${token}/status`);
        if (!resp.ok) return;
        const data = await resp.json();
        setMode(String(data?.mode || ""));
        setStatus(String(data?.status || "pending"));
      } catch {
        // ignore
      }
    };
    fetchStatus();
  }, [token]);

  useEffect(() => {
    if (!token || status === "expired") return undefined;
    if (!window.isSecureContext) {
      setCameraError("");
      setCameraNotice("Camera requires HTTPS on iPhone. Open the https:// link.");
      return undefined;
    }
    if (!navigator?.mediaDevices?.getUserMedia) {
      setCameraError("");
      setCameraNotice("Camera requires HTTPS on iPhone. Open the https:// link.");
      return undefined;
    }
    let cancelled = false;
    const startCamera = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
        }
      } catch (err) {
        setCameraError(err?.message || "Camera unavailable");
      }
    };
    startCamera();
    return () => {
      cancelled = true;
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
    };
  }, [token, status]);

  useEffect(() => {
    pagesRef.current = pages;
  }, [pages]);

  useEffect(() => {
    capturedRef.current = captured;
  }, [captured]);

  useEffect(() => () => {
    if (capturedRef.current?.previewUrl) {
      URL.revokeObjectURL(capturedRef.current.previewUrl);
    }
    pagesRef.current.forEach((page) => {
      if (page.previewUrl) URL.revokeObjectURL(page.previewUrl);
    });
  }, []);

  const handleCapture = async () => {
    if (!videoRef.current || uploading || status === "expired") return;
    if (!videoRef.current.videoWidth) {
      setMessage("Camera not ready. Try again.");
      return;
    }
    setMessage("Processing scan...");
    const result = await captureFromVideo(videoRef.current, cvReady);
    if (!result?.blob) {
      setMessage("Capture failed. Try again.");
      return;
    }
    setCaptured(result);
    setMessage("");
  };

  const handleRetake = () => {
    if (captured?.previewUrl) URL.revokeObjectURL(captured.previewUrl);
    setCaptured(null);
  };

  const addPage = (page) => {
    setPages((prev) => [...prev, page]);
  };

  const handleUseCapture = () => {
    if (!captured) return;
    if (mode === "master_key") {
      setPages((prev) => {
        prev.forEach((page) => {
          if (page.previewUrl) URL.revokeObjectURL(page.previewUrl);
        });
        return [captured];
      });
    } else {
      addPage(captured);
    }
    setCaptured(null);
  };

  const removePage = (index) => {
    setPages((prev) => {
      const next = [...prev];
      const [removed] = next.splice(index, 1);
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return next;
    });
  };

  const resetPages = () => {
    setPages((prev) => {
      prev.forEach((page) => {
        if (page.previewUrl) URL.revokeObjectURL(page.previewUrl);
      });
      return [];
    });
  };

  const buildStudentPdf = async () => {
    const { PDFDocument } = await loadPdfLib();
    const pdfDoc = await PDFDocument.create();
    for (const page of pages) {
      const bytes = await page.blob.arrayBuffer();
      const image = page.blob.type === "image/png"
        ? await pdfDoc.embedPng(bytes)
        : await pdfDoc.embedJpg(bytes);
      const width = page.width || image.width;
      const height = page.height || image.height;
      const pdfPage = pdfDoc.addPage([width, height]);
      pdfPage.drawImage(image, { x: 0, y: 0, width, height });
    }
    const pdfBytes = await pdfDoc.save();
    return new Blob([pdfBytes], { type: "application/pdf" });
  };

  const uploadMasterKey = async () => {
    if (!pages.length) return;
    setUploading(true);
    setMessage("Uploading master key...");
    try {
      const form = new FormData();
      const first = pages[0];
      form.append("file", first.blob, "scan.jpg");
      const uploadUrl = `${apiBase()}/api/scan/${token}/upload`;
      const resp = await fetch(uploadUrl, { method: "POST", body: form });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = data?.detail || "Upload failed";
        throw new Error(`Status ${resp.status}: ${detail}`);
      }
      setStatus("complete");
      setMessage("Master key saved ✅ You can close this tab.");
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Upload failed",
        description: err?.message || "Upload failed",
      });
      setMessage(err?.message || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const finishStudent = async () => {
    if (!pages.length) return;
    setUploading(true);
    setMessage("Building PDF...");
    try {
      const pdfBlob = await buildStudentPdf();
      const sizes = pages.map((page) => ({
        width_px: page.width,
        height_px: page.height,
      }));
      const form = new FormData();
      form.append("file", pdfBlob, "scan.pdf");
      form.append("page_count", String(pages.length));
      form.append("page_sizes", JSON.stringify(sizes));
      const uploadUrl = `${apiBase()}/api/scan/${token}/upload`;
      const resp = await fetch(uploadUrl, { method: "POST", body: form });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = data?.detail || "Upload failed";
        throw new Error(`Status ${resp.status}: ${detail}`);
      }
      setMessage("Saved ✅ Scan next student.");
      resetPages();
    } catch (err) {
      toast({
        variant: "destructive",
        title: "Upload failed",
        description: err?.message || "Upload failed",
      });
      setMessage(err?.message || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const canCapture = status !== "expired" && !uploading && Boolean(mode);
  const canFinishStudent = mode === "student" && pages.length > 0 && !uploading && status !== "expired";
  const canSaveMasterKey = mode === "master_key" && pages.length > 0 && !uploading && status !== "expired";

  return (
    <div className="min-h-screen bg-white text-slate-900 p-6">
      <div className="max-w-2xl mx-auto space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">{scanLabel(mode)}</h1>
        <p className="text-sm text-slate-600">
          Hold steady and fill the frame. We will straighten the page before saving.
        </p>
        {status === "expired" && (
          <p className="text-sm text-red-600 mt-2">This scan session expired. Scan a new QR.</p>
        )}
        {cameraNotice && (
          <p className="text-sm text-slate-600 mt-2">{cameraNotice}</p>
        )}
        {cameraError && (
          <p className="text-sm text-red-600 mt-2">Camera error: {cameraError}</p>
        )}
          {cvError && (
            <p className="text-xs text-slate-500 mt-1">OpenCV disabled: {cvError}</p>
          )}
        </div>

        <div className="rounded-xl border border-slate-200 p-4 space-y-4">
          <div className="relative aspect-[3/4] w-full overflow-hidden rounded-lg bg-slate-100">
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className="h-full w-full object-cover"
            />
            <div className="pointer-events-none absolute inset-6 border-2 border-dashed border-white/80 rounded" />
          </div>

          {captured && (
            <div className="space-y-3">
              <div>
                <div className="text-sm font-medium">Preview</div>
                <img
                  src={captured.previewUrl}
                  alt="Scan preview"
                  className="mt-2 w-full rounded-md border border-slate-200"
                />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={handleRetake} disabled={uploading}>
                  Retake
                </Button>
                <Button onClick={handleUseCapture} disabled={uploading}>
                  Use page
                </Button>
              </div>
            </div>
          )}

          {!captured && (
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={handleCapture} disabled={!canCapture}>
                {uploading ? "Working..." : "Capture page"}
              </Button>
              {mode === "student" && pages.length > 0 && (
                <Button variant="outline" onClick={resetPages} disabled={uploading}>
                  Clear packet
                </Button>
              )}
            </div>
          )}

          {message && <div className="text-sm text-slate-700">{message}</div>}
        </div>

        {mode === "student" && (
          <div className="rounded-xl border border-slate-200 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium">Current student packet</div>
                <div className="text-xs text-slate-500">{pages.length} page(s)</div>
              </div>
              <Button onClick={finishStudent} disabled={!canFinishStudent}>
                Finish student
              </Button>
            </div>
            {pages.length === 0 ? (
              <div className="text-sm text-slate-500">Capture pages to build a packet.</div>
            ) : (
              <div className="flex gap-3 overflow-x-auto pb-2">
                {pages.map((page, idx) => (
                  <div key={`${page.previewUrl}-${idx}`} className="relative">
                    <img
                      src={page.previewUrl}
                      alt={`Page ${idx + 1}`}
                      className="h-28 w-20 rounded border border-slate-200 object-cover"
                    />
                    <button
                      type="button"
                      onClick={() => removePage(idx)}
                      className="absolute -top-2 -right-2 h-6 w-6 rounded-full bg-white text-xs text-slate-700 shadow"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {mode === "master_key" && (
          <div className="rounded-xl border border-slate-200 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium">Master key capture</div>
                <div className="text-xs text-slate-500">
                  Use one clean, rectified page.
                </div>
              </div>
              <Button onClick={uploadMasterKey} disabled={!canSaveMasterKey}>
                Save master key
              </Button>
            </div>
            {pages.length === 0 && (
              <div className="text-sm text-slate-500">Capture a page to save.</div>
            )}
            {pages.length > 0 && (
              <img
                src={pages[0].previewUrl}
                alt="Master key preview"
                className="w-full rounded-md border border-slate-200"
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
