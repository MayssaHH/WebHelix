"""Read-only backend check for the two scripted admin controls, never agent input."""

import argparse
import json
import subprocess
from pathlib import Path

PHP = r"""
$cfg = require '/var/www/magento2/app/etc/env.php';
$db = $cfg['db']['connection']['default'];
$pdo = new PDO('mysql:host='.$db['host'].';dbname='.$db['dbname'],
               $db['username'], $db['password']);
$price = $pdo->query("SELECT p.entity_id, p.sku, d.store_id, d.value AS price
  FROM catalog_product_entity p
  JOIN catalog_product_entity_decimal d ON d.entity_id=p.entity_id
  JOIN eav_attribute a ON a.attribute_id=d.attribute_id
  WHERE p.entity_id=1481 AND a.attribute_code='price' ORDER BY d.store_id")
  ->fetchAll(PDO::FETCH_ASSOC);
$orders = $pdo->query("SELECT status, COUNT(*) AS count FROM sales_order
  GROUP BY status ORDER BY status")->fetchAll(PDO::FETCH_ASSOC);
echo json_encode(['product_1481_prices'=>$price, 'order_status_counts'=>$orders]);
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="webhelix-wa-shopping-admin")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite backend evidence")
    inspect = subprocess.run(
        ["docker", "inspect", "--type", "container", args.container],
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    metadata = json.loads(inspect.stdout)[0]
    if (
        metadata["Config"].get("Labels", {}).get("org.webhelix.benchmark")
        != "shopping_admin"
    ):
        raise ValueError("Not an owned Shopping Admin deployment")
    result = subprocess.run(
        ["docker", "exec", args.container, "php", "-r", PHP],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    state = json.loads(result.stdout)
    state["container_id"] = metadata["Id"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
