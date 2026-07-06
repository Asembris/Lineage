/*
 * ConsistencyScene3D — Frontend Phase 5, the ONE sanctioned react-three-fiber feature, now an
 * interactive forensic instrument (Phase 5.1).
 *
 * A 3D rendering of the SAME real GET /demo/consistency/stream samples the 2D view consumes:
 * the belief's inherited closure as distributed holder nodes in space. It renders the SAME
 * derived state the 2D DrainMeter does — `total_edges` nodes, `total - open` shown corrected —
 * so 2D and 3D can never disagree; only the presentation differs.
 *
 * The contrast this makes visible (the audit's worst-ranked criticism — atomic-across-regions
 * argued-not-demonstrated):
 *   - EVENTUAL: nodes flip one-by-one as the real per-holder samples drain, and while the
 *     closure is torn the still-open nodes glow --alert (a real committed SPLIT — laggards still
 *     live on the dead belief). The torn state is VISIBLE, not narrated.
 *   - STRONG (the real atomic endpoint): open_edges jumps 8→0 in a single sample, so every node
 *     flips to --alive together — one commit, no torn frame ever.
 *
 * IDENTITY (Phase 5.1). The SSE stream carries COUNTS, not holder ids — so node i is bound to a
 * REAL holder via GET /beliefs/{id}/lineage, ordered by `inherited_at`. That order is not a guess:
 * the eventual fan-out (app/services/consistency.py) closes edges `ORDER BY inherited_at`, so the
 * k-th edge to close IS the k-th holder by inherited_at. This component only takes indices +
 * highlight state; the holder facts and the closure-sample mapping live in the parent (which owns
 * the lineage + samples). onHover/onSelect report the node index; the parent resolves identity.
 *
 * Tokens only: corrected=--alive, torn=--alert, at-rest=--ash, background=--void, edges=--line,
 * hover/select accent=--bone. NO amber/orange (that stays Trace's). Motion is emissive/color lerp
 * + a small scale pulse; prefers-reduced-motion SNAPS every node to its current state (no lerp,
 * no pulse). Camera: user-driven OrbitControls only — no autoRotate, no flythrough; damping is
 * disabled under reduced motion so there is zero non-user motion.
 */

import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { OrbitControls as OrbitControlsImpl } from "three/examples/jsm/controls/OrbitControls.js";
import type { ConsistencySampleEvent, ConsistencyState } from "../api/types";

// Token colours (mirrors tokens.css / FRONTEND.md — kept literal here because three needs hex,
// not CSS custom properties, and these are the same values the 2D view resolves).
const VOID = "#0A0E14"; // --void
const LINE = "#243040"; // --line
const BONE = "#C4CDD8"; // --bone : the neutral bright, used ONLY for the hover/select accent
const COLOR = {
  rest: "#5A6678", // --ash   : open edge, belief live, closure not torn
  torn: "#E5484D", // --alert : open edge during a committed SPLIT (laggard on the dead belief)
  corrected: "#3FE0A8", // --alive : edge closed (this holder corrected)
} as const;
const EMISSIVE = { rest: 0.12, torn: 0.9, corrected: 0.5 } as const;
type Kind = keyof typeof COLOR;

const TOTAL_FALLBACK = 8;
const RADIUS = 2.05;
const LERP = 0.14;

/** Deterministic fibonacci-shell positions — "distributed in space", stable across renders. */
function holderPositions(n: number): THREE.Vector3[] {
  const golden = Math.PI * (3 - Math.sqrt(5));
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i < n; i++) {
    const y = n === 1 ? 0 : 1 - (i / (n - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = i * golden;
    pts.push(
      new THREE.Vector3(Math.cos(theta) * r, y, Math.sin(theta) * r).multiplyScalar(RADIUS),
    );
  }
  return pts;
}

function kindForIndex(i: number, closed: number, state: ConsistencyState): Kind {
  if (i < closed) return "corrected";
  return state === "SPLIT" ? "torn" : "rest";
}

/** User-driven orbit/zoom via three's own examples/jsm control (zero new deps). No autoRotate,
 *  no pan; damping (a brief post-drag glide) is disabled under reduced motion so nothing moves
 *  unless the user is actively dragging. */
function Controls({ reducedMotion }: { reducedMotion: boolean }) {
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  const controls = useMemo(() => new OrbitControlsImpl(camera, gl.domElement), [camera, gl]);
  useEffect(() => {
    controls.enablePan = false;
    controls.enableDamping = !reducedMotion;
    controls.dampingFactor = 0.09;
    controls.rotateSpeed = 0.6;
    controls.zoomSpeed = 0.8;
    controls.minDistance = 4;
    controls.maxDistance = 13;
    return () => controls.dispose();
  }, [controls, reducedMotion]);
  useFrame(() => controls.update());
  return null;
}

/** One holder edge. Lerps colour/emissive toward its target kind and pulses once on a flip; hover
 *  and selection add a --bone accent (brighter emissive + a small scale-up, plus a wireframe halo
 *  when selected). Under reduced motion everything snaps and never pulses. */
function HolderNode({
  position,
  kind,
  reducedMotion,
  index,
  hovered,
  selected,
  interactive,
  onHover,
  onSelect,
}: {
  position: THREE.Vector3;
  kind: Kind;
  reducedMotion: boolean;
  index: number;
  hovered: boolean;
  selected: boolean;
  interactive: boolean;
  onHover?: (i: number | null) => void;
  onSelect?: (i: number | null) => void;
}) {
  const matRef = useRef<THREE.MeshStandardMaterial>(null);
  const meshRef = useRef<THREE.Mesh>(null);
  const target = useMemo(() => new THREE.Color(COLOR[kind]), [kind]);
  const prevKind = useRef<Kind>(kind);
  const pulse = useRef(0);

  // Derived-from-props: detect a flip during render (allowed ref write) so we pulse only on an
  // actual state change, never on mount (prevKind starts equal to kind → no initial pop).
  if (prevKind.current !== kind) {
    prevKind.current = kind;
    if (!reducedMotion) pulse.current = 1;
  }

  useFrame(() => {
    const mat = matRef.current;
    const mesh = meshRef.current;
    if (!mat || !mesh) return;
    const emTarget = EMISSIVE[kind] + (hovered || selected ? 0.45 : 0);
    const scaleBase = hovered ? 1.22 : selected ? 1.12 : 1;
    if (reducedMotion) {
      mat.color.set(target);
      mat.emissive.set(target);
      mat.emissiveIntensity = emTarget;
      mesh.scale.setScalar(scaleBase);
      return;
    }
    mat.color.lerp(target, LERP);
    mat.emissive.lerp(target, LERP);
    mat.emissiveIntensity += (emTarget - mat.emissiveIntensity) * LERP;
    pulse.current = pulse.current < 0.01 ? 0 : pulse.current * 0.9;
    mesh.scale.setScalar(scaleBase + pulse.current * 0.28);
  });

  const handlers = interactive
    ? {
        onPointerOver: (e: { stopPropagation: () => void }) => {
          e.stopPropagation();
          onHover?.(index);
          document.body.style.cursor = "pointer";
        },
        onPointerOut: (e: { stopPropagation: () => void }) => {
          e.stopPropagation();
          onHover?.(null);
          document.body.style.cursor = "";
        },
        onClick: (e: { stopPropagation: () => void }) => {
          e.stopPropagation();
          onSelect?.(selected ? null : index);
        },
      }
    : {};

  return (
    <group position={position}>
      <mesh ref={meshRef} {...handlers}>
        <sphereGeometry args={[0.34, 32, 32]} />
        <meshStandardMaterial
          ref={matRef}
          color={COLOR.rest}
          emissive={COLOR.rest}
          emissiveIntensity={EMISSIVE.rest}
          roughness={0.35}
          metalness={0.12}
        />
      </mesh>
      {selected && (
        <mesh raycast={() => null}>
          <sphereGeometry args={[0.47, 24, 24]} />
          <meshBasicMaterial color={BONE} wireframe transparent opacity={0.5} />
        </mesh>
      )}
    </group>
  );
}

/** A thin --line edge from the belief core to a holder node — the inheritance edge that makes
 *  the closure a closure. Structural context; the flip story lives on the nodes. */
function Edge({ target }: { target: THREE.Vector3 }) {
  const { mid, quaternion, length } = useMemo(() => {
    const dir = target.clone();
    const len = dir.length();
    const quat = new THREE.Quaternion().setFromUnitVectors(
      new THREE.Vector3(0, 1, 0),
      dir.clone().normalize(),
    );
    return { mid: dir.multiplyScalar(0.5), quaternion: quat, length: len };
  }, [target]);
  return (
    <mesh position={mid} quaternion={quaternion} raycast={() => null}>
      <cylinderGeometry args={[0.012, 0.012, length, 8]} />
      <meshStandardMaterial color={LINE} emissive={LINE} emissiveIntensity={0.2} />
    </mesh>
  );
}

/** The belief at the centre of its closure — a neutral --ghost wireframe anchor (the holders,
 *  not the core, carry the flip). */
function BeliefCore() {
  return (
    <mesh raycast={() => null}>
      <icosahedronGeometry args={[0.44, 0]} />
      <meshStandardMaterial color="#8A94A6" emissive="#8A94A6" emissiveIntensity={0.18} wireframe />
    </mesh>
  );
}

export function ConsistencyScene3D({
  samples,
  reducedMotion,
  interactive = false,
  hoveredIndex = null,
  selectedIndex = null,
  onHover,
  onSelect,
}: {
  samples: ConsistencySampleEvent[];
  reducedMotion: boolean;
  interactive?: boolean;
  hoveredIndex?: number | null;
  selectedIndex?: number | null;
  onHover?: (i: number | null) => void;
  onSelect?: (i: number | null) => void;
}) {
  const latest = samples[samples.length - 1];
  const total = latest?.total_edges ?? TOTAL_FALLBACK;
  const open = latest?.open_edges ?? total;
  const state: ConsistencyState = latest?.state ?? "ALL_ACTIVE";
  const closed = total - open;

  const positions = useMemo(() => holderPositions(total), [total]);

  return (
    <div
      className="cx3d"
      role="img"
      aria-label={`Closure in 3D: ${closed} of ${total} holder edges corrected, state ${state}`}
    >
      <Canvas
        camera={{ position: [0, 0.6, 7.4], fov: 42 }}
        dpr={[1, 2]}
        gl={{ antialias: true }}
        onPointerMissed={() => onSelect?.(null)}
      >
        <color attach="background" args={[VOID]} />
        <ambientLight intensity={0.6} />
        <directionalLight position={[4, 6, 5]} intensity={2.2} />
        <directionalLight position={[-5, -2, -3]} intensity={0.7} />
        <Controls reducedMotion={reducedMotion} />
        <BeliefCore />
        {positions.map((p, i) => (
          <Edge key={`e${i}`} target={p} />
        ))}
        {positions.map((p, i) => (
          <HolderNode
            key={`n${i}`}
            position={p}
            kind={kindForIndex(i, closed, state)}
            reducedMotion={reducedMotion}
            index={i}
            hovered={hoveredIndex === i}
            selected={selectedIndex === i}
            interactive={interactive}
            onHover={onHover}
            onSelect={onSelect}
          />
        ))}
      </Canvas>
    </div>
  );
}
