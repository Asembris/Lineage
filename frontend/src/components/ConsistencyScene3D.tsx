/*
 * ConsistencyScene3D — Frontend Phase 5, the ONE sanctioned react-three-fiber feature.
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
 * Honesty: the stream gives COUNTS, not holder identities, so which node fills is presentational
 * (same note as the 2D meter) — a node is never labelled as a specific agent. Node positions are
 * deterministic (a fibonacci shell, no Math.random) so the scene is stable across renders/shots.
 *
 * Tokens only: corrected=--alive, torn=--alert, at-rest=--ash, background=--void, edges=--line.
 * NO amber/orange (that stays Trace's). Motion is emissive/color lerp + a small scale pulse
 * (transforms/opacity-class per CLAUDE.md); prefers-reduced-motion SNAPS every node to its
 * current-sample state (no lerp, no pulse) and the camera is static in BOTH modes (no orbit,
 * no flythrough — scope discipline).
 */

import { Canvas, useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import type { ConsistencySampleEvent, ConsistencyState } from "../api/types";

// Token colours (mirrors tokens.css / FRONTEND.md — kept literal here because three needs hex,
// not CSS custom properties, and these are the same values the 2D view resolves).
const VOID = "#0A0E14"; // --void
const LINE = "#243040"; // --line
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

/** One holder edge. Lerps colour/emissive toward its target kind and pulses once on a flip;
 *  under reduced motion it snaps and never pulses. */
function HolderNode({
  position,
  kind,
  reducedMotion,
}: {
  position: THREE.Vector3;
  kind: Kind;
  reducedMotion: boolean;
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
    if (reducedMotion) {
      mat.color.set(target);
      mat.emissive.set(target);
      mat.emissiveIntensity = EMISSIVE[kind];
      mesh.scale.setScalar(1);
      return;
    }
    mat.color.lerp(target, LERP);
    mat.emissive.lerp(target, LERP);
    mat.emissiveIntensity += (EMISSIVE[kind] - mat.emissiveIntensity) * LERP;
    pulse.current = pulse.current < 0.01 ? 0 : pulse.current * 0.9;
    mesh.scale.setScalar(1 + pulse.current * 0.28);
  });

  return (
    <mesh ref={meshRef} position={position}>
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
    <mesh position={mid} quaternion={quaternion}>
      <cylinderGeometry args={[0.012, 0.012, length, 8]} />
      <meshStandardMaterial color={LINE} emissive={LINE} emissiveIntensity={0.2} />
    </mesh>
  );
}

/** The belief at the centre of its closure — a neutral --ghost wireframe anchor (the holders,
 *  not the core, carry the flip). */
function BeliefCore() {
  return (
    <mesh>
      <icosahedronGeometry args={[0.44, 0]} />
      <meshStandardMaterial color="#8A94A6" emissive="#8A94A6" emissiveIntensity={0.18} wireframe />
    </mesh>
  );
}

export function ConsistencyScene3D({
  samples,
  reducedMotion,
}: {
  samples: ConsistencySampleEvent[];
  reducedMotion: boolean;
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
      <Canvas camera={{ position: [0, 0.6, 7.4], fov: 42 }} dpr={[1, 2]} gl={{ antialias: true }}>
        <color attach="background" args={[VOID]} />
        <ambientLight intensity={0.6} />
        <directionalLight position={[4, 6, 5]} intensity={2.2} />
        <directionalLight position={[-5, -2, -3]} intensity={0.7} />
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
          />
        ))}
      </Canvas>
    </div>
  );
}
