(* CLI entry point for the OCaml geospatial port.

   Usage:
     corestack_geocompute drainage --mws MWS.geojson --lines LINES.geojson --out OUT.geojson *)

let usage () =
  prerr_endline "usage: corestack_geocompute drainage --mws <f> --lines <f> --out <f>";
  exit 2

let arg_val flag argv =
  let rec go = function
    | a :: v :: _ when a = flag -> Some v
    | _ :: tl -> go tl
    | [] -> None
  in
  go (Array.to_list argv)

let drainage argv =
  let req flag = match arg_val flag argv with Some v -> v | None -> usage () in
  let mws = req "--mws" and lines = req "--lines" and out = req "--out" in
  let n = Geocompute.Drainage.run ~mws ~lines ~out in
  Printf.printf "drainage: %d watersheds -> %s\n%!" n out

let () =
  match Array.to_list Sys.argv with
  | _ :: "drainage" :: _ -> drainage Sys.argv
  | _ -> usage ()
