(* Drainage density — pure-OCaml port of
   computing/clart/drainage_density.py::generate_vector  (spec: ../../myref/formuale.md §4)

   Pure OCaml (only yojson). Implements the three primitives the Python relies on:
     - reproject EPSG:4326 -> EPSG:7755 (Lambert Conformal Conic 2SP, ellipsoidal)
     - clip drainage lines to a watershed polygon (segment/polygon, in the projected plane)
     - geometry length (metres) in the projected CRS
   then DD = sum_order ( len_km * influence_factor * 100 / (area_in_ha/100) ).

   No GDAL/GEOS/PROJ dependency — this is the genuinely-OCaml computation we verify
   against the Python reference. *)

(* ---------- EPSG:7755 : WGS84 / India NSF LCC (2SP), from epsg.io/7755 ---------- *)
let pi = 4.0 *. atan 1.0
let d2r d = d *. pi /. 180.0
let a = 6378137.0                       (* WGS84 semi-major axis *)
let f = 1.0 /. 298.257223563            (* WGS84 inverse flattening *)
let e2 = (2.0 *. f) -. (f *. f)
let e = sqrt e2
let phi0 = d2r 24.0
let lam0 = d2r 80.0
let phi1 = d2r 12.472955
let phi2 = d2r 35.1728044444444
let fe = 4_000_000.0
let fn = 4_000_000.0

let m_of phi = cos phi /. sqrt (1.0 -. (e2 *. sin phi *. sin phi))
let t_of phi =
  let s = sin phi in
  tan ((pi /. 4.0) -. (phi /. 2.0))
  /. (((1.0 -. (e *. s)) /. (1.0 +. (e *. s))) ** (e /. 2.0))

let n_c = (log (m_of phi1) -. log (m_of phi2)) /. (log (t_of phi1) -. log (t_of phi2))
let big_f = m_of phi1 /. (n_c *. (t_of phi1 ** n_c))
let r0 = a *. big_f *. (t_of phi0 ** n_c)

(* (lon,lat) degrees -> (easting,northing) metres *)
let project (lon, lat) =
  let phi = d2r lat and lam = d2r lon in
  let r = a *. big_f *. (t_of phi ** n_c) in
  let theta = n_c *. (lam -. lam0) in
  (fe +. (r *. sin theta), fn +. r0 -. (r *. cos theta))

(* ---------- influence factors, stream orders 1..11 (verbatim) ---------- *)
let factors =
  [| 60. /. 385.; 55. /. 385.; 50. /. 385.; 45. /. 385.; 40. /. 385.; 35. /. 385.
   ; 30. /. 385.; 25. /. 385.; 20. /. 385.; 15. /. 385.; 10. /. 385. |]

(* ---------- GeoJSON helpers (yojson) ---------- *)
let num = function
  | `Float x -> x | `Int i -> float_of_int i | `Intlit s -> float_of_string s
  | _ -> nan
let mem k = function `Assoc kv -> (try List.assoc k kv with Not_found -> `Null) | _ -> `Null
let str_of = function `String s -> s | _ -> ""
let pos = function `List (x :: y :: _) -> (num x, num y) | _ -> (nan, nan)
let ring = function `List ps -> List.map pos ps | _ -> []
let features j = match mem "features" j with `List l -> l | _ -> []

(* geometry -> list of polygons; each polygon = ring list (head exterior, tail holes) *)
let polygons geom =
  let coords = mem "coordinates" geom in
  match str_of (mem "type" geom) with
  | "Polygon" -> [ (match coords with `List rs -> List.map ring rs | _ -> []) ]
  | "MultiPolygon" ->
    (match coords with
     | `List ps -> List.map (function `List rs -> List.map ring rs | _ -> []) ps
     | _ -> [])
  | _ -> []

(* geometry -> list of polylines *)
let polylines geom =
  let coords = mem "coordinates" geom in
  match str_of (mem "type" geom) with
  | "LineString" -> [ ring coords ]
  | "MultiLineString" -> (match coords with `List ls -> List.map ring ls | _ -> [])
  | _ -> []

(* ---------- planar geometry (projected coords) ---------- *)
let close r = match r with [] -> [] | h :: _ -> r @ [ h ]

(* point-in-ring, ray casting *)
let pip (px, py) r =
  let rec go inside = function
    | (x1, y1) :: ((x2, y2) :: _ as tl) ->
      let cross =
        (y1 > py) <> (y2 > py)
        && px < (((x2 -. x1) *. (py -. y1) /. (y2 -. y1)) +. x1)
      in
      go (if cross then not inside else inside) tl
    | _ -> inside
  in
  go false (close r)

(* polygon (ext::holes) and multipolygon containment *)
let in_poly pt = function
  | ext :: holes -> pip pt ext && not (List.exists (pip pt) holes)
  | [] -> false
let in_mp pt mp = List.exists (in_poly pt) mp

(* all edges of a multipolygon as (a,b) pairs *)
let edges mp =
  List.concat_map
    (fun poly ->
      List.concat_map
        (fun r ->
          let r = close r in
          let rec pairs = function a :: (b :: _ as tl) -> (a, b) :: pairs tl | _ -> [] in
          pairs r)
        poly)
    mp

(* intersection parameter t in (0,1) of segment a->b with edge c->d *)
let seg_t (ax, ay) (bx, by) (cx, cy) (dx, dy) =
  let rx = bx -. ax and ry = by -. ay and sx = dx -. cx and sy = dy -. cy in
  let denom = (rx *. sy) -. (ry *. sx) in
  if abs_float denom < 1e-15 then None
  else
    let t = (((cx -. ax) *. sy) -. ((cy -. ay) *. sx)) /. denom in
    let u = (((cx -. ax) *. ry) -. ((cy -. ay) *. rx)) /. denom in
    if t > 0.0 && t < 1.0 && u >= 0.0 && u <= 1.0 then Some t else None

let dist (ax, ay) (bx, by) = sqrt (((bx -. ax) ** 2.0) +. ((by -. ay) ** 2.0))
let lerp (ax, ay) (bx, by) t = (ax +. (t *. (bx -. ax)), ay +. (t *. (by -. ay)))

(* length of segment a->b that lies inside the multipolygon mp *)
let seg_inside_len mp es a b =
  let ts =
    List.filter_map (fun (c, d) -> seg_t a b c d) es
    |> List.sort_uniq compare
  in
  let ts = 0.0 :: (ts @ [ 1.0 ]) in
  let rec go acc = function
    | t0 :: (t1 :: _ as tl) ->
      let p0 = lerp a b t0 and p1 = lerp a b t1 in
      let mid = lerp a b ((t0 +. t1) /. 2.0) in
      go (if in_mp mid mp then acc +. dist p0 p1 else acc) tl
    | _ -> acc
  in
  go 0.0 ts

(* clipped length (metres) of a projected polyline inside the multipolygon *)
let polyline_inside_len mp pl =
  let es = edges mp in
  let rec go acc = function
    | a :: (b :: _ as tl) -> go (acc +. seg_inside_len mp es a b) tl
    | _ -> acc
  in
  go 0.0 pl

(* ---------- the pipeline ---------- *)
let run ~mws ~lines ~out =
  let mws_j = Yojson.Safe.from_file mws and lines_j = Yojson.Safe.from_file lines in

  (* pre-project every drainage line once: (order, projected polyline) *)
  let plines =
    List.concat_map
      (fun feat ->
        let order =
          match mem "ORDER" (mem "properties" feat) with
          | `Int i -> i | `Float x -> int_of_float x | _ -> 0
        in
        List.map
          (fun pl -> (order, List.map project pl))
          (polylines (mem "geometry" feat)))
      (features lines_j)
  in

  let out_feats =
    List.map
      (fun feat ->
        let props = mem "properties" feat in
        let geom = mem "geometry" feat in
        let area_in_ha = num (mem "area_in_ha" props) in
        let area = area_in_ha /. 100.0 in
        let mp = List.map (List.map (List.map project)) (polygons geom) in
        let len_km = Array.make 11 0.0 in
        List.iter
          (fun (order, pl) ->
            if order >= 1 && order <= 11 then
              len_km.(order - 1) <-
                len_km.(order - 1) +. (polyline_inside_len mp pl /. 1000.0))
          plines;
        let dd = Array.init 11 (fun i -> len_km.(i) *. factors.(i) *. 100.0 /. area) in
        let dd_total = Array.fold_left ( +. ) 0.0 dd in
        let flist arr = `List (Array.to_list (Array.map (fun x -> `Float x) arr)) in
        `Assoc
          [ ("type", `String "Feature")
          ; ( "properties"
            , `Assoc
                [ ("uid", mem "uid" props)
                ; ("area_in_ha", `Float area_in_ha)
                ; ("DD", `Float dd_total)
                ; ("DD_stream", flist dd)
                ; ("str_len_km", flist len_km)
                ] )
          ; ("geometry", geom)
          ])
      (features mws_j)
  in
  let fc = `Assoc [ ("type", `String "FeatureCollection"); ("features", `List out_feats) ] in
  Yojson.Safe.to_file out fc;
  List.length out_feats
